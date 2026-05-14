"""Instagram follow-scout runner.

Walks active competitor sources from data/competitors.json, scouts
followers + recent-post engagers, filters by NA-likely heuristic and
already-followed history, then follows up to the daily ceiling.

Usage:
    ig_follow.py                    # scout + follow within daily cap
    ig_follow.py --dry-run          # scout + report, NO follow actions
    ig_follow.py --health-check     # verify state + exit (no IG calls)
    ig_follow.py --max-follows N    # override the per-run follow cap
    ig_follow.py --max-candidates N # cap on total candidates scouted
    ig_follow.py --source HANDLE    # restrict to one source (debug)

Failure modes:
    IGActionBlockedError → abort batch, mark run unsuccessful, next
        cron tick retries with a fresh attempt. Do NOT retry within
        the same process — IG escalates blocks on rapid retries.
    LockAcquisitionError → another instance is running, exit cleanly.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))
sys.path.insert(0, str(PROJECT_ROOT))

from ig_follow.candidate import Candidate
from ig_follow.constants import DAILY_FOLLOW_CEILING, FOLLOW_JITTER_SECONDS
from ig_follow.exceptions import IGActionBlockedError, IGUserNotFoundError
from ig_follow.follower import FollowOutcome, follow_user
from ig_follow.geo_filter import is_north_america_likely
from ig_follow.scout_engagers import scout_engagers
from ig_follow.scout_followers import scout_followers
from ig_follow.state import follows_today, is_already_followed, record_follow
from ig_follow.targets import ig_sources
from local_env import load_local_env
from notifier import skill_error, skill_finished, skill_skipped, skill_started
from observability.correlation_id import new_correlation_id
from observability.logger import configure_logging, get_logger
from runtime.singleton import LockAcquisitionError, SingletonLock
from sessions.browser import ig_session

if TYPE_CHECKING:
    from playwright.sync_api import Page

LOG_FILE = PROJECT_ROOT / "logs" / "engagement_log.jsonl"
SESSION_FILE = PROJECT_ROOT / ".claude" / "state" / "instagram_session.json"
SKILL_NAME = "ig-follow-scout"

log = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="Scout + report only.")
    p.add_argument("--health-check", action="store_true", help="Verify state and exit.")
    p.add_argument("--max-follows", type=int, default=DAILY_FOLLOW_CEILING)
    p.add_argument("--max-candidates", type=int, default=200)
    p.add_argument("--source", type=str, default=None, help="Restrict to one source handle.")
    return p.parse_args()


def _health_check() -> int:
    """Verify the pieces needed to run. Returns 0 on success, 1 otherwise."""
    problems: list[str] = []
    if not SESSION_FILE.exists():
        problems.append(f"IG session file missing: {SESSION_FILE} — run scripts/ig_login.py first")
    sources = ig_sources()
    if not sources:
        problems.append("No active sources with ig_handle in data/competitors.json")
    today = follows_today()
    log.info(
        "health_check",
        session_exists=SESSION_FILE.exists(),
        sources=len(sources),
        follows_today=today,
        daily_ceiling=DAILY_FOLLOW_CEILING,
    )
    if problems:
        for line in problems:
            log.error("health_check_problem", detail=line)
        return 1
    return 0


def _candidate_should_skip(cand: Candidate) -> tuple[bool, str]:
    """Return (skip, reason). reason is empty when skip is False."""
    if is_already_followed(cand.handle):
        return True, "already_followed"
    if cand.follower_count is not None and cand.follower_count > 50_000:
        return True, "too_many_followers"
    geo = is_north_america_likely(cand.bio, cand.display_name)
    if geo is False:
        return True, "geo_filter_rejected"
    return False, ""


def _gather_candidates(page: Page, max_candidates: int, only_source: str | None) -> list[Candidate]:
    """Walk sources round-robin; alternate followers + engagers per source."""
    sources = ig_sources()
    if only_source:
        sources = [s for s in sources if s.handle == only_source.lower().lstrip("@")]
        if not sources:
            log.warning("source_not_found", source=only_source)
            return []

    out: list[Candidate] = []
    seen: set[str] = set()
    for src in sources:
        if len(out) >= max_candidates:
            break
        log.info("scouting_source", source=src.handle, niche=src.niche)
        for scout_fn, label in ((scout_followers, "followers"), (scout_engagers, "engagers")):
            if len(out) >= max_candidates:
                break
            try:
                cands = scout_fn(page, src.handle)
            except IGUserNotFoundError:
                log.warning("source_404", source=src.handle, signal=label)
                break
            except IGActionBlockedError:
                raise
            except Exception as exc:
                log.warning(
                    "scout_failed", source=src.handle, signal=label, error=str(exc)
                )
                continue
            log.info("scout_yielded", source=src.handle, signal=label, count=len(cands))
            for c in cands:
                if c.handle in seen:
                    continue
                seen.add(c.handle)
                out.append(c)
                if len(out) >= max_candidates:
                    break
    return out


def _filter_candidates(cands: list[Candidate]) -> tuple[list[Candidate], dict[str, int]]:
    """Apply skip rules. Returns (kept, reason_counts)."""
    kept: list[Candidate] = []
    counts: dict[str, int] = {}
    for c in cands:
        skip, reason = _candidate_should_skip(c)
        if skip:
            counts[reason] = counts.get(reason, 0) + 1
            continue
        kept.append(c)
    counts["kept"] = len(kept)
    return kept, counts


def _append_engagement_log(action: str, payload: dict[str, object]) -> None:
    entry = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now(UTC).isoformat() + "Z",
        "platform": "instagram",
        "action": action,
        **payload,
    }
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _execute_follows(page: Page, candidates: list[Candidate], budget: int) -> dict[str, int]:
    """Walk candidates, follow with jitter, persist history. Stops on budget or block."""
    counts: dict[str, int] = {"followed": 0, "requested": 0, "already": 0, "no_button": 0}
    jitter_lo, jitter_hi = FOLLOW_JITTER_SECONDS

    for cand in candidates:
        if counts["followed"] + counts["requested"] >= budget:
            log.info("budget_reached", budget=budget)
            break

        try:
            result = follow_user(page, cand.handle)
        except IGUserNotFoundError:
            log.warning("target_404", handle=cand.handle)
            continue

        if result.outcome is FollowOutcome.FOLLOWED:
            counts["followed"] += 1
        elif result.outcome is FollowOutcome.REQUESTED:
            counts["requested"] += 1
        elif result.outcome is FollowOutcome.ALREADY_FOLLOWING:
            counts["already"] += 1
        else:
            counts["no_button"] += 1

        if result.outcome in (FollowOutcome.FOLLOWED, FollowOutcome.REQUESTED):
            record_follow(cand.handle, cand.source_handle, cand.source_signal)
            _append_engagement_log(
                "ig_follow",
                {
                    "target_handle": cand.handle,
                    "source_handle": cand.source_handle,
                    "source_signal": cand.source_signal,
                    "outcome": result.outcome.value,
                },
            )

        log.info(
            "follow_attempted",
            handle=cand.handle,
            outcome=result.outcome.value,
            source=cand.source_handle,
            signal=cand.source_signal,
        )

        time.sleep(random.randint(jitter_lo, jitter_hi))

    return counts


def _print_dry_run(candidates: list[Candidate], reason_counts: dict[str, int]) -> None:
    print(f"\nCandidates kept: {len(candidates)}")
    print(f"Filter breakdown: {reason_counts}")
    print(f"Daily ceiling: {DAILY_FOLLOW_CEILING} (today: {follows_today()})")
    print("\nFirst 25 candidates:")
    for c in candidates[:25]:
        print(f"  - @{c.handle:30s} via @{c.source_handle} ({c.source_signal})")


def main() -> int:
    load_local_env()
    configure_logging()
    args = _parse_args()

    if args.health_check:
        return _health_check()

    with new_correlation_id(SKILL_NAME):
        skill_started(SKILL_NAME, "dry-run" if args.dry_run else "live")
        try:
            with SingletonLock(SKILL_NAME):
                return _run(args)
        except LockAcquisitionError as exc:
            log.warning("lock_held", detail=str(exc))
            skill_skipped(SKILL_NAME, f"another instance running: {exc}")
            return 0
        except IGActionBlockedError as exc:
            log.error("ig_action_blocked", detail=str(exc))
            skill_error(SKILL_NAME, f"IG action-blocked: {exc}")
            return 2
        except Exception as exc:
            log.exception("unhandled", error=str(exc))
            skill_error(SKILL_NAME, f"unhandled: {type(exc).__name__}: {exc}")
            return 1


def _run(args: argparse.Namespace) -> int:
    today = follows_today()
    budget = max(0, min(args.max_follows, DAILY_FOLLOW_CEILING) - today)
    log.info("run_started", follows_today=today, budget=budget, dry_run=args.dry_run)

    if budget == 0 and not args.dry_run:
        skill_skipped(SKILL_NAME, f"daily ceiling already met ({today})")
        return 0

    with ig_session() as page:
        candidates = _gather_candidates(page, args.max_candidates, args.source)
        kept, reason_counts = _filter_candidates(candidates)
        log.info("candidates_filtered", **reason_counts)

        if args.dry_run:
            _print_dry_run(kept, reason_counts)
            skill_finished(SKILL_NAME, f"dry-run: {len(kept)} candidates")
            return 0

        counts = _execute_follows(page, kept, budget)

    summary = (
        f"followed={counts['followed']} requested={counts['requested']} "
        f"already={counts['already']} no_button={counts['no_button']}"
    )
    skill_finished(SKILL_NAME, summary)
    log.info("run_finished", **counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
