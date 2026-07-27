#!/usr/bin/env python3
"""TikTok scout — state management and candidate reporting CLI.

Scraping is handled by the tiktok-scout Claude skill (uses claude-in-chrome).
This script manages state and reports candidates.

Usage:
    tiktok_scout.py                    # load + filter + report
    tiktok_scout.py --apply            # report pending count
    tiktok_scout.py --dry-run          # list first 25 pending candidates
    tiktok_scout.py --health-check     # verify deps + exit
    tiktok_scout.py --max-candidates N # override per-run candidate cap
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.bootstrap import init_script

settings, _bootstrap_log = init_script(__name__)
sys.path.insert(0, str(PROJECT_ROOT))

from lib.tiktok_scout import (
    DAILY_SCOUT_CEILING,
    FOLLOWER_MAX,
    FOLLOWER_MIN,
    TikTokCandidate,
    candidates_today,
    is_already_seen,
    is_north_america_likely,
    load_candidates,
)
from local_env import load_local_env
from notifier import skill_error, skill_finished, skill_skipped, skill_started
from observability.correlation_id import new_correlation_id
from observability.logger import configure_logging, get_logger
from runtime.singleton import LockAcquisitionError, SingletonLock

SKILL_NAME = "tiktok-scout"

log = get_logger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--dry-run", action="store_true", default=False, help="List first 25 pending candidates, do NOT save.")
    p.add_argument("--apply", action="store_true", help="Report pending count ready for follow-worker.")
    p.add_argument("--health-check", action="store_true", help="Verify deps and exit.")
    p.add_argument("--max-candidates", type=int, default=DAILY_SCOUT_CEILING)
    return p.parse_args(argv)


def _health_check() -> bool:
    """Verify runtime prerequisites. Prints [OK]/[FAIL] lines. Returns True on success."""
    problems: list[str] = []

    brand_dir = getattr(settings, "brand_dir", None) or os.environ.get("BRAND_DIR")
    if not brand_dir:
        problems.append("BRAND_DIR env var not set")

    candidates_dir = PROJECT_ROOT / ".claude" / "state"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    if not os.access(candidates_dir, os.W_OK):
        problems.append(f"candidates dir not writable: {candidates_dir}")

    if not settings.paths:
        problems.append("settings.paths not initialized — BRAND_DIR may be missing")
        session_ok = False
    else:
        session_ok = settings.paths.tiktok_session.exists()
        if not session_ok:
            problems.append(f"tiktok_session file not found: {settings.paths.tiktok_session}")

    today = candidates_today()
    log.info("health_check", brand_dir_ok=bool(brand_dir), session_ok=session_ok,
             candidates_today=today, daily_ceiling=DAILY_SCOUT_CEILING)

    if problems:
        for detail in problems:
            print(f"[FAIL] {detail}")
            log.error("health_check_problem", detail=detail)
        return False

    print(f"[OK] BRAND_DIR set: {brand_dir}")
    print(f"[OK] candidates dir writable: {candidates_dir}")
    print(f"[OK] tiktok_session file exists")
    print(f"[OK] candidates today: {today}/{DAILY_SCOUT_CEILING}")
    return True


def _filter_candidates(candidates: list[TikTokCandidate]) -> list[TikTokCandidate]:
    """Apply skip rules: seen, follower range, geo heuristic."""
    kept: list[TikTokCandidate] = []
    counts: dict[str, int] = {"already_seen": 0, "follower_range": 0, "geo_rejected": 0}

    for c in candidates:
        if is_already_seen(c.handle):
            counts["already_seen"] += 1
            continue
        if c.follower_count != 0 and not (FOLLOWER_MIN <= c.follower_count <= FOLLOWER_MAX):
            counts["follower_range"] += 1
            continue
        if is_north_america_likely(c.bio, c.display_name) is False:
            counts["geo_rejected"] += 1
            continue
        kept.append(c)

    log.info("filter_complete", kept=len(kept), **counts)
    return kept


def _run(args: argparse.Namespace) -> int:
    today_count = candidates_today()
    if today_count >= DAILY_SCOUT_CEILING:
        log.info("daily_ceiling_reached", count=today_count, ceiling=DAILY_SCOUT_CEILING)
        skill_skipped(SKILL_NAME, f"daily ceiling reached ({today_count}/{DAILY_SCOUT_CEILING})")
        return 0

    all_candidates = load_candidates()
    pending = _filter_candidates(all_candidates)[: args.max_candidates]

    log.info(
        "scout_summary",
        total_loaded=len(all_candidates),
        after_filter=len(pending),
        dry_run=args.dry_run,
        apply=args.apply,
    )

    print(f"\nCandidates loaded:  {len(all_candidates)}")
    print(f"After filter:       {len(pending)}")
    print(f"Daily ceiling:      {DAILY_SCOUT_CEILING} (today: {today_count})")

    if args.apply:
        print(f"\nPending for follow-worker: {len(pending)}")
        log.info("apply_reported", pending=len(pending))

    if args.dry_run:
        print("\nFirst 25 pending candidates:")
        for c in pending[:25]:
            print(f"  - @{c.handle:35s} followers={c.follower_count:,}  #{c.source_hashtag}")

    skill_finished(SKILL_NAME, f"{len(pending)} candidates pending")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_local_env()
    configure_logging()
    args = _parse_args(argv)

    if args.health_check:
        return 0 if _health_check() else 1

    with new_correlation_id(SKILL_NAME):
        skill_started(SKILL_NAME, "apply" if args.apply else "dry-run")
        try:
            with SingletonLock(SKILL_NAME):
                return _run(args)
        except LockAcquisitionError as exc:
            log.warning("lock_held", detail=str(exc))
            skill_skipped(SKILL_NAME, f"another instance running: {exc}")
            return 0
        except Exception as exc:
            log.exception("unhandled", error=str(exc))
            skill_error(SKILL_NAME, f"unhandled: {type(exc).__name__}: {exc}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
