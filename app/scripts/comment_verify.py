#!/usr/bin/env python3
"""Reconcile unsettled comment claims against the platforms themselves.

`lib/comment_outbox.py` writes a `comment_claims` row BEFORE the comment is
submitted and flips it to `posted` only once the submission comes back
confirmed. A row still `pending` therefore means exactly one of two things:

* the comment landed and the confirmation was lost (the process died between
  the submit and the settle), or
* the comment never landed at all.

**Nothing inside the process that took the claim can tell those apart.**
`lib/engagement/comment_confirm.py` only gets to look while the submitting
process is still alive, and `lib/engagement/adapters/instagram.py` maps *any*
exception -- including one raised after the comment was already accepted -- to
`CommentResult.failed(...)`. Only the post itself knows. This flow goes back
and looks, which is what makes the outbox safe rather than merely strict.

Three outcomes, and the third is the whole point:

* our comment IS on the post -> `settle`, plus the engagement records the
  failed run never got to write, so a reconciled comment is indistinguishable
  downstream from a clean one.
* we read the post's comments and ours is NOT among them -> `release`. The
  post is genuinely retryable and the next scan may comment on it.
* we could not see the post at all -- nav timeout, login wall, tombstoned post
  -- -> **leave the claim pending**. "Could not see" is not "did not post".
  Collapsing inconclusive into absent is precisely how this design turns back
  into the duplicate comment it exists to prevent, so an inconclusive lookup is
  counted and otherwise ignored; the next run looks again.

A claim older than `MAX_AGE_DAYS` is settled without a browser visit at all:
it is past the point where anyone could confirm it, and a claim nobody can
ever confirm must become permanent rather than become a duplicate. Fail closed.

Both platforms are verified in one run, independently: an expired Instagram
session skips Instagram and Facebook is still reconciled.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.bootstrap import init_script
from lib.worker_labels import worker_label_for_flow

settings, log = init_script(__name__)

from lib import comment_outbox, deduplication, engagements_db
from lib.comment_outbox import PendingClaim
from lib.comment_verify_sessions import PLATFORM_SESSIONS as _PLATFORMS
from lib.engagement.log import log_engagement
from lib.engagement.result import CommentLookup
from lib.notifier import skill_finished, skill_started
from lib.runtime.flow import run_flow

FLOW_ID = "comment-verify"
WORKER_LABEL = worker_label_for_flow(FLOW_ID)

# Never race a submission still in flight: a claim younger than this may belong
# to an engager that is mid-post right now, and looking at the post would read
# "absent" a second before the comment appears.
STALE_MINUTES = 15
# Fail-closed backstop. Past this age the post has moved on, the session that
# made the claim is long gone, and no lookup will ever be trustworthy.
MAX_AGE_DAYS = 30
DEFAULT_LIMIT = 25  # per platform, per run
PLATFORMS: tuple[str, ...] = ("instagram", "facebook")


@dataclass(frozen=True)
class VerifyReport:
    """What one reconciliation run decided.

    `checked` counts claims this run reached a decision on, so claims left
    behind by a skipped platform are absent from every counter -- a run that
    could not open Instagram must not look like a run that found nothing there.
    """

    checked: int = 0
    settled: int = 0
    released: int = 0
    inconclusive: int = 0
    aged_out: int = 0


def _plus(report: VerifyReport, **deltas: int) -> VerifyReport:
    """Return `report` with the named counters incremented."""
    return replace(report, **{k: getattr(report, k) + v for k, v in deltas.items()})


def _age_days(claim: PendingClaim, now: datetime) -> float:
    """Age of the claim in days. A naive `claimed_at` is read as UTC."""
    claimed = claim.claimed_at
    if claimed.tzinfo is None:
        claimed = claimed.replace(tzinfo=UTC)
    return (now - claimed).total_seconds() / 86400.0


def _settle_confirmed(claim: PendingClaim) -> None:
    """Write the records the failed run never got to write.

    A comment reconciled days later must be indistinguishable downstream from
    one that settled cleanly, otherwise the engagement history, the published
    view and the dedup cache each disagree about whether it happened.

    Deliberately absent: `rate_limiter`. The comment budget was spent on the
    day the comment was actually submitted; charging it again here would bill
    the same comment twice and silently shrink a later day's quota.
    """
    engagements_db.record_publish(
        platform=claim.platform,
        kind="comment",
        status="posted",
        target_name=claim.target_name,
        target_url=claim.post_url,
        content=claim.content,
        ref=claim.post_id,
        posted_at=claim.claimed_at.isoformat(),
    )
    log_engagement(
        "comment",
        claim.platform,
        claim.target_name,
        claim.content,
        post_url=claim.post_url,
        post_id=claim.post_id,
    )
    deduplication.mark_engaged(claim.platform, claim.post_id, "comment", claim.target_name)


def _age_out(claim: PendingClaim, report: VerifyReport, *, dry_run: bool) -> VerifyReport:
    """Settle a claim too old to verify, without visiting the post."""
    log.info("comment_verify_aged_out platform=%s post_id=%s", claim.platform, claim.post_id)
    if not dry_run:
        comment_outbox.settle(claim.platform, claim.post_id, brand=claim.brand)
    return _plus(report, checked=1, aged_out=1)


def _adjudicate(
    claim: PendingClaim,
    lookup: CommentLookup,
    report: VerifyReport,
    *,
    dry_run: bool,
) -> VerifyReport:
    """Turn one lookup into one outbox decision.

    The `not conclusive` branch is load-bearing. A nav timeout or a login wall
    means we learned nothing, and releasing on "nothing" is the duplicate-
    comment bug all over again -- the claim would be freed and the next scan
    would comment on a post that already carries our comment. So an
    inconclusive lookup changes no state at all; the claim waits for a run that
    can actually see the post.
    """
    if lookup.present:
        log.info("comment_verify_settled platform=%s post_id=%s", claim.platform, claim.post_id)
        if not dry_run:
            comment_outbox.settle(claim.platform, claim.post_id, brand=claim.brand)
            _settle_confirmed(claim)
        return _plus(report, checked=1, settled=1)
    if lookup.conclusive and lookup.count == 0:
        log.info("comment_verify_released platform=%s post_id=%s", claim.platform, claim.post_id)
        if not dry_run:
            comment_outbox.release(claim.platform, claim.post_id, brand=claim.brand)
        return _plus(report, checked=1, released=1)
    log.info(
        "comment_verify_inconclusive platform=%s post_id=%s reason=%s",
        claim.platform,
        claim.post_id,
        lookup.reason,
    )
    return _plus(report, checked=1, inconclusive=1)


def _verify_platform(
    platform: str,
    claims: Sequence[PendingClaim],
    report: VerifyReport,
    *,
    dry_run: bool,
) -> VerifyReport:
    """Adjudicate one platform's claims. Never raises.

    A session that will not open (expired cookies, no browser) skips this
    platform and leaves its claims pending -- it is evidence about our access,
    not about whether those comments landed -- while the other platform is
    still reconciled by the caller.
    """
    now = datetime.now(UTC)
    fresh: list[PendingClaim] = []
    for claim in claims:
        if _age_days(claim, now) > MAX_AGE_DAYS:
            report = _age_out(claim, report, dry_run=dry_run)
        else:
            fresh.append(claim)
    if not fresh:
        return report
    open_page, find_own_comment = _PLATFORMS[platform]
    try:
        with open_page() as page:
            for claim in fresh:
                lookup = find_own_comment(page, claim.post_url, claim.content)
                report = _adjudicate(claim, lookup, report, dry_run=dry_run)
    except Exception as exc:
        log.warning("comment_verify_platform_skipped platform=%s error=%s", platform, exc)
    return report


def run_verify(
    *,
    platforms: Sequence[str] = PLATFORMS,
    dry_run: bool = False,
    limit: int = DEFAULT_LIMIT,
    minutes: int = STALE_MINUTES,
) -> VerifyReport:
    """Reconcile every claim still pending after `minutes`.

    The work queue is fetched in ONE query and then partitioned by platform, so
    a run costs a single round-trip regardless of how many platforms it covers.
    The query asks for `limit` per platform and each platform's slice is capped
    at `limit`, so one platform's backlog can never starve the other's.
    """
    pending = comment_outbox.pending_older_than(minutes, limit=limit * max(1, len(platforms)))
    report = VerifyReport()
    for platform in platforms:
        claims = [c for c in pending if c.platform == platform][:limit]
        if claims:
            report = _verify_platform(platform, claims, report, dry_run=dry_run)
    return report


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the CLI. Unknown flags (`--health-check`) are left to `run_flow`."""
    parser = argparse.ArgumentParser(
        description="Reconcile unsettled comment claims against the platforms."
    )
    parser.add_argument(
        "--platform",
        action="append",
        choices=list(PLATFORMS),
        help="Verify only this platform (repeatable). Default: all.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Look at every post and report, but settle/release nothing.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Max claims per platform per run (default {DEFAULT_LIMIT}).",
    )
    return parser.parse_known_args(list(sys.argv[1:] if argv is None else argv))[0]


def _summary(report: VerifyReport, *, dry_run: bool) -> str:
    prefix = "DRY RUN (nothing settled or released) | " if dry_run else ""
    return (
        f"{prefix}Checked: {report.checked} | Settled: {report.settled} | "
        f"Released: {report.released} | Inconclusive: {report.inconclusive} | "
        f"Aged out: {report.aged_out}"
    )


def _flow_main(args: argparse.Namespace) -> None:
    """Adapt `run_verify` to `run_flow`'s `int | None` exit-code contract.

    `args` is parsed by the caller, OUTSIDE `run_flow`, on purpose: argparse
    exits the process on `--help`, and doing that in here raises `SystemExit`
    between `record_start` and `record_complete`, leaving a `worker_runs` row
    open forever — the Schedule page would show this flow stuck "running"
    because someone typed `--help`. `scripts/fb_group_scout.py` does the same.
    """
    platforms = tuple(args.platform) if args.platform else PLATFORMS
    label = "DRY RUN — " if args.dry_run else ""
    skill_started(FLOW_ID, f"{label}Reconciling unsettled comment claims")
    report = run_verify(platforms=platforms, dry_run=args.dry_run, limit=args.limit)
    summary = _summary(report, dry_run=args.dry_run)
    print(summary)
    skill_finished(FLOW_ID, summary)


if __name__ == "__main__":
    # Parsed here, before `run_flow` opens a `worker_runs` row — see `_flow_main`.
    _args = _parse_args()

    # No `health_check=`: verify spans both platforms and already degrades one
    # at a time, so a single session probe would fail the whole flow over a
    # platform it would have skipped anyway.
    raise SystemExit(run_flow(FLOW_ID, lambda: _flow_main(_args), worker_label=WORKER_LABEL))
