"""Check on FB group join requests stuck in admin-approval limbo.

Revisits every group in the groups DB with status=join_requested, navigates
to the group's page, scrapes the visible text, and hands it to
``lib.group_discovery.status_classifier.classify_join_outcome`` to decide
what happened to our join request. The per-group classify/staleness/rejoin
decisions live in ``lib.group_discovery.join_recheck`` (file-size rule);
this script owns CLI parsing, the visit loop, rate-limit gating, and
worker/singleton-lock bookkeeping.

  - "joined" → status=joined, joined_at=now, note appended.
  - "rejected" → status=rejected, note appended with the LLM's reason.
  - "still_pending" → if past ``--stale-days``, status=stale_pending with a
    note; otherwise left as join_requested (last_checked_at bumped).
  - "unknown" (ambiguous page text) → same as still_pending-not-yet-stale:
    left as join_requested, last_checked_at bumped, ambiguity logged.
  - classifier returns None (LLM/classification failure) → status left
    unchanged, last_checked_at bumped, logged, move on.

See ``lib.group_discovery.join_recheck``'s module docstring for exactly
which timestamp is used as the staleness anchor and why (there is no
`join_requested_at` column).

Classify-only by default: this script NEVER sends a join request on its
own. ``--force-rejoin`` is a manual-trigger-only flag that, when passed,
attempts a fresh ``try_join()`` for any group that ends this run at
status=rejected or status=stale_pending, bypassing fb_group_scout.py's
daily/weekly join cap (an explicit override, same bypass semantics as that
script's own ``--bypass-daily-cap``).

Safe by default: navigation + read-only scraping only. No posting, no join
requests, unless ``--force-rejoin`` is explicitly passed.

Usage:
    python -m scripts.fb_pending_joins_check                    # check every join_requested entry
    python -m scripts.fb_pending_joins_check --stale-days 3      # flag stale > 3d
    python -m scripts.fb_pending_joins_check --dry-run           # classify only, write nothing
    python -m scripts.fb_pending_joins_check --force-rejoin      # + re-attempt rejected/stale groups
    python -m scripts.fb_pending_joins_check --health-check      # verify session + exit
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.bootstrap import init_script

settings, log = init_script(__name__)

from lib.fb.session import FbSession, build_fb_session
from lib.group_discovery.fb_search import pace_between_joins
from lib.group_discovery.join_recheck import process_entry, rejoin_entry
from lib.runtime.singleton import LockAcquisitionError, SingletonLock
from lib.worker_db import record_complete, record_start
from lib.worker_labels import worker_label_for_flow
from notifier import skill_error, skill_finished, skill_started
from rate_limiter import can_act, record_action

SKILL_NAME = "fb-pending-joins-check"
WORKER_LABEL = worker_label_for_flow(SKILL_NAME)

if settings.paths is None:
    raise RuntimeError("settings.paths is unset; lib.config failed to resolve BRAND_DIR")
from lib import groups_db  # FB groups live in groups.db (was groups_tracker.json)
from lib.groups_db.models import GroupStatus


def _visit_gate() -> bool:
    """Shared facebook:group_visit budget (6/day) — same gate+consume
    pattern as lib.engagement.post_processor.gate_source / fb_engager.py."""
    if not can_act("facebook", "group_visit"):
        return False
    record_action("facebook", "group_visit")
    return True


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check on pending Facebook group join requests")
    parser.add_argument("--stale-days", type=int, default=5, help="flag as stale after N days")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="classify only, don't write groups_db or attempt rejoins",
    )
    parser.add_argument(
        "--force-rejoin",
        action="store_true",
        help=(
            "manually re-send try_join() for any group ending this run at "
            "rejected/stale_pending, bypassing the daily/weekly join cap "
            "(never runs automatically — must be explicitly passed)"
        ),
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="verify FB session is authenticated and exit",
    )
    return parser.parse_args()


def _health_check(session: FbSession) -> int:
    if not session.is_authenticated():
        print(f"SESSION_EXPIRED: {session.storage_path}", file=sys.stderr)
        return 1
    print(f"FB session OK (storage: {session.storage_path})")
    return 0


def _run_classify_loop(page: Any, pool: list[dict[str, Any]], stale_days: int) -> tuple[dict[str, int], int | None]:
    counts = {
        "joined": 0, "rejected": 0, "stale_pending": 0,
        "still_pending": 0, "unknown": 0, "classify_failed": 0,
    }
    budget_exhausted_at = None
    for i, entry in enumerate(pool):
        if not _visit_gate():
            log.info(f"group-visit budget exhausted — stopping ({i}/{len(pool)} checked)")
            budget_exhausted_at = i
            break
        log.info(f"→ {entry.get('group_name', '?')[:45]}")
        outcome = process_entry(page, entry, stale_days)
        print(f"    → {outcome}", flush=True)
        counts[outcome] += 1
        if i < len(pool) - 1:
            pace_between_joins(is_last=False)
    return counts, budget_exhausted_at


def _run_rejoin_loop(page: Any, pool: list[dict[str, Any]]) -> tuple[int, int]:
    """Re-attempt join for groups THIS run flipped to rejected/stale_pending."""
    candidates = [
        e for e in pool if e.get("status") in (GroupStatus.REJECTED, GroupStatus.STALE_PENDING)
    ]
    if not candidates:
        return 0, 0
    print(
        f"\n--force-rejoin: attempting {len(candidates)} fresh join request(s) "
        "(bypassing daily/weekly join cap)…",
        flush=True,
    )
    attempted = sent = 0
    for j, entry in enumerate(candidates):
        if not _visit_gate():
            log.info("group-visit budget exhausted — stopping rejoin attempts")
            break
        log.info(f"↻ {entry.get('group_name', '?')[:45]}")
        result = rejoin_entry(page, entry)
        print(f"    → {result}", flush=True)
        attempted += 1
        if result == "clicked":
            sent += 1
        if j < len(candidates) - 1:
            pace_between_joins(is_last=False)
    return attempted, sent


def main(
    session: FbSession, *, dry_run: bool = False, stale_days: int = 5, force_rejoin: bool = False
) -> int:
    skill_started(SKILL_NAME, "checking pending join requests")

    if not session.is_authenticated():
        skill_error(SKILL_NAME, "FB session missing — run login.py fb")
        return 1

    tracker: list[dict[str, Any]] = groups_db.load_all()
    pool = [e for e in tracker if e.get("status") == GroupStatus.JOIN_REQUESTED]
    if not pool:
        msg = "no pending join requests to check"
        print(msg, flush=True)
        skill_finished(SKILL_NAME, msg)
        return 0

    print(f"checking {len(pool)} pending join request(s)…", flush=True)

    rejoin_attempted = rejoin_sent = 0
    with session.page() as page:
        counts, budget_exhausted_at = _run_classify_loop(page, pool, stale_days)
        if force_rejoin and not dry_run:
            rejoin_attempted, rejoin_sent = _run_rejoin_loop(page, pool)

    if dry_run:
        print("\n(dry-run — groups_db not written, no rejoin attempts)", flush=True)
    else:
        groups_db.save_all(tracker)

    summary = (
        f"joined={counts['joined']} rejected={counts['rejected']} "
        f"stale={counts['stale_pending']} still_pending={counts['still_pending']} "
        f"unknown={counts['unknown']} classify_failed={counts['classify_failed']}"
    )
    if budget_exhausted_at is not None:
        summary += f" | budget_exhausted_after={budget_exhausted_at}"
    if force_rejoin:
        summary += f" | rejoin_attempted={rejoin_attempted} rejoin_sent={rejoin_sent}"
    print(f"\n=== Done === {summary}", flush=True)
    skill_finished(SKILL_NAME, summary)
    return 0


if __name__ == "__main__":
    args = _parse_args()
    fb_session = build_fb_session()

    if args.health_check:
        sys.exit(_health_check(fb_session))

    _brand_dir = settings.paths.brand_dir
    _brand = _brand_dir.name

    try:
        with SingletonLock(SKILL_NAME):
            record_start(_brand_dir, WORKER_LABEL, _brand)
            try:
                exit_code = main(
                    fb_session,
                    dry_run=args.dry_run,
                    stale_days=args.stale_days,
                    force_rejoin=args.force_rejoin,
                )
                record_complete(
                    _brand_dir, WORKER_LABEL, _brand, "success" if exit_code == 0 else "error"
                )
            except Exception as exc:
                record_complete(_brand_dir, WORKER_LABEL, _brand, "error", str(exc))
                raise
            sys.exit(exit_code)
    except LockAcquisitionError as exc:
        print(f"another instance of {SKILL_NAME!r} is running: {exc}", file=sys.stderr)
        sys.exit(0)
