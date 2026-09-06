"""Instagram Hashtag Scanner — SINGLE PASS like + comment.

Orchestration lives in `lib.engagement.pipeline`; platform mechanics live
in `InstagramHashtagAdapter`. This wrapper builds the collaborators,
calls the pipeline, and persists the last-run stamp.

Each post is opened exactly once: the scan scores it, likes it, and — when
it clears the auto-approve threshold — drafts and posts the comment in that
same visit. There is no Redis queue and no `scripts/ig_comment.py` handoff
for Instagram any more (Facebook keeps its two-stage scan -> queue -> comment
flow). Iterate-once is enforced by `lib.scan_dedup.ScanDedup`, which marks
every OPENED post so the next run skips it.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.activity_log import log_trace
from lib.bootstrap import init_script
from lib.worker_labels import worker_label_for_flow

settings, log = init_script(__name__)

WORKER_LABEL = worker_label_for_flow("ig-engager")

from lib import draft_helper, rate_limiter
from lib.comment_generator import score_relevance as _score_relevance
from lib.engagement.adapter import OutboundAdapter
from lib.engagement.adapters.instagram import InstagramHashtagAdapter
from lib.engagement.pipeline import ScanReport, run_outbound_scan
from lib.engagement.policy import EngagementPolicy, thresholds_from_config
from lib.engagement.post import Post
from lib.engagement.run_summary import build_summary, log_funnel
from lib.engagement.scan_deadline import (
    DeadlineAwareRateTracker,
    deadline_from_env,
)
from lib.io.jsonio import read_json, write_json
from lib.notifier import skill_finished, skill_skipped, skill_started
from lib.rate_limiter import can_act, daily_limit, print_status
from lib.runtime.flow import run_flow, session_file_check
from lib.scan_dedup import ScanDedup

LAST_RUN_FILE = settings.paths.last_run
SESSION_FILE = settings.paths.instagram_session
CONFIG_FILE = settings.paths.brand_dir / "config.json"
HASHTAG_FILE = settings.paths.instagram_accounts

# Bound at import: the system prompt comes from .claude/skills/ig-engager/
# SKILL.md, so a broken or missing skill file (SkillPromptError) aborts the
# run at startup — before any hashtag is scanned — matching
# scripts/ig_comment.py's abort-loudly pattern. The per-post USER prompt is
# built by the drafter per call.
_DRAFTER = draft_helper.for_skill("ig-engager")


def _score_post(post: Post) -> float:
    """Adapt the pipeline's `(Post) -> float` callable to the real
    `score_relevance(text, post_meta)` signature.

    Restores the IG meta signal dropped during the slice-3 pipeline
    extraction. Mirrors the pre-pipeline call in slice 2 (commit 856013e):
    IG hard-coded `hours_old=12` and did not pass `group_category`.
    """
    comment_count_raw = post.platform_extra.get("comment_count", 0) or 0
    return _score_relevance(
        post.text,
        {"comment_count": int(comment_count_raw), "hours_old": 12},  # type: ignore[call-overload]
    )


def _already_ran_today(last_run: dict[str, Any]) -> bool:
    """True only for a COMPLETE pass earlier today.

    A pass that stopped at its own deadline stamps `"truncated"`, not
    `"success"`, so it deliberately does not close the day: the hashtags it
    never reached are still worth a second attempt, and `ScanDedup` makes
    that attempt cheap (every post the first pass opened is skipped).
    """
    ig = last_run.get("ig_engager", {})
    return (ig.get("last_run_at") or "")[:10] == date.today().isoformat() and ig.get(
        "status"
    ) == "success"


def run_ig_scan(
    adapter: OutboundAdapter | None = None, *, dry_run: bool | None = None
) -> ScanReport | None:
    """Run one IG hashtag scan via the shared pipeline.

    ``dry_run`` defaults to ``"--dry-run" in sys.argv``. A dry run likes
    nothing and posts no comment, and consumes no state (no last-run stamp,
    no dedup marks) — so it can be re-run freely. It DOES still call the
    drafter, so the preview shows the comment each qualifying post would
    have received.
    """
    if dry_run is None:
        dry_run = "--dry-run" in sys.argv
    log_trace("instagram", "Started Instagram hashtag scan")
    last_run: dict[str, Any] = read_json(LAST_RUN_FILE, default={})  # type: ignore[assignment]
    # Both daily guards are live-run concerns: a dry run neither stamps
    # last_run.json nor sends a like, so blocking it would force --force --
    # which ALSO lifts the like rate cap. Preview stays freely re-runnable.
    if not dry_run and _already_ran_today(last_run) and "--force" not in sys.argv:
        skill_skipped("ig-engager", "already ran successfully today")
        log_trace("instagram", "Skipped: already ran today")
        return None
    if not dry_run and not can_act("instagram", "like") and "--force" not in sys.argv:
        skill_skipped("ig-engager", "Daily IG like limit reached")
        print_status()
        return None

    label = "DRY RUN — " if dry_run else ""
    skill_started(
        "ig-engager",
        f"{label}Scanning Instagram hashtags for posts to like/comment",
    )
    print_status()

    config: dict[str, Any] = read_json(CONFIG_FILE, default={})  # type: ignore[assignment]
    policy = EngagementPolicy.from_enforced_limits(
        thresholds=thresholds_from_config(config),
    )
    active = adapter or InstagramHashtagAdapter(
        {**config, "session_file": SESSION_FILE, "hashtag_file": HASHTAG_FILE}
    )
    # The worker's own `subprocess.run(timeout=)` fuse, handed down as
    # FLOW_TIMEOUT_SECONDS. None when the flow was started by hand. A full
    # pass normally finishes well inside it; this is what makes a SLOW one
    # end with a summary and a stamp instead of a SIGKILL.
    deadline = deadline_from_env()
    try:
        report = run_outbound_scan(
            active,
            policy,
            # Single pass: the drafter runs INSIDE the scan so a qualifying
            # post is liked and commented in the one visit that opened it.
            # No queue, no ig_comment.py handoff (see module docstring).
            dedup=ScanDedup(WORKER_LABEL, log=log),
            rate_tracker=DeadlineAwareRateTracker(rate_limiter, deadline),
            drafter=_DRAFTER,
            log=log,
            now_iso=lambda: datetime.now(UTC).isoformat(),
            score_relevance=_score_post,
            dry_run=dry_run,
            inline_comment=True,
            deadline=deadline,
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "SESSION_EXPIRED" in msg or "No saved Instagram session" in msg:
            log_trace("instagram", f"Aborted: {msg}")
            skill_skipped("ig-engager", msg)
            return None
        raise

    # A dry run consumes no state: no dedup marks (the pipeline suppresses
    # them, so posts stay eligible) and no last-run stamp (the
    # already-ran-today guard is not burned).
    if not dry_run:
        last_run["ig_engager"] = {
            "last_run_at": datetime.now(UTC).isoformat(),
            "hashtags_scanned": report.sources_visited,
            "hashtags_due": report.sources_total,
            "posts_liked": report.likes_succeeded,
            "posts_commented": report.comments_posted,
            "comments_declined": report.comments_declined,
            # Only a pass that reached the end of the day's hashtag list is
            # "success". A truncated one is stamped so it is visible in
            # `scripts/status.py` (anything but success/FAILED renders as a
            # warning) AND so `_already_ran_today` does not use it to skip
            # the rest of the day.
            "status": "success" if report.stopped_reason is None else "truncated",
            "stopped_reason": report.stopped_reason,
        }
        write_json(LAST_RUN_FILE, last_run)

    # The funnel goes to BOTH sinks: the summary is what a human reads in
    # Telegram, the log line is what survives to be grepped afterwards.
    log_funnel(report, log)
    summary = build_summary(
        report,
        source_label="Hashtags",
        comment_quota=daily_limit("instagram", "comment"),
        dry_run=dry_run,
    )
    skill_finished("ig-engager", summary)
    print_status()
    return report


def _flow_main() -> None:
    """Adapt the scan to `run_flow`'s exit-code contract.

    `run_ig_scan` returns a `ScanReport` because its callers and tests
    want one. `run_flow` wants `int | None`, an exit code. Passing the scan
    straight through handed the report to `SystemExit`, which treats a
    non-int as an error MESSAGE and exits 1 -- so every successful run was
    recorded as a failure. Discarding the report here keeps both contracts
    honest.
    """
    run_ig_scan()


if __name__ == "__main__":
    raise SystemExit(
        run_flow(
            "ig-engager",
            _flow_main,
            health_check=lambda: session_file_check(SESSION_FILE, "IG"),
            worker_label=WORKER_LABEL,
        )
    )
