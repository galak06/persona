"""Instagram Hashtag Scanner — thin wrapper around `run_outbound_scan`.

Orchestration lives in `lib.engagement.pipeline`; platform mechanics live
in `InstagramHashtagAdapter`. This wrapper builds the collaborators,
calls the pipeline, persists last-run + dedup marks.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from lib.activity_log import log_trace
from lib.bootstrap import init_script

settings, log = init_script(__name__)

import deduplication
import draft_helper
import rate_limiter
from comment_generator import score_relevance as _score_relevance
from lib.engagement.adapter import OutboundAdapter
from lib.engagement.adapters.instagram import InstagramHashtagAdapter
from lib.engagement.pipeline import ScanReport, run_outbound_scan
from lib.engagement.policy import EngagementPolicy
from notifier import skill_finished, skill_skipped, skill_started
from rate_limiter import can_act, print_status

QUEUE_FILE = settings.paths.comment_queue
LAST_RUN_FILE = settings.paths.last_run
SESSION_FILE = settings.paths.instagram_session
CONFIG_FILE = settings.paths.brand_dir / "config.json"
HASHTAG_FILE = settings.paths.instagram_accounts


class _QueueIO:
    """`run_outbound_scan` queue collaborator: append + atomic save + today-count."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._existing: list[dict[str, Any]] = (
            json.loads(path.read_text()) if path.exists() else []
        )
        self.newly_queued: list[dict[str, Any]] = []

    def append(self, record: dict[str, object]) -> None:
        rec = dict(record)
        self._existing.append(rec)  # type: ignore[arg-type]
        self.newly_queued.append(rec)  # type: ignore[arg-type]

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._existing, indent=2))

    def existing_today(self, platform: str) -> int:
        today = date.today().isoformat()
        return sum(
            1 for q in self._existing
            if q.get("platform") == platform
            and str(q.get("queued_at", "")).startswith(today)
            and q not in self.newly_queued
        )


def _load_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


def _already_ran_today(last_run: dict[str, Any]) -> bool:
    ig = last_run.get("ig_scanner", {})
    return (ig.get("last_run_at") or "")[:10] == date.today().isoformat() and ig.get(
        "status"
    ) == "success"


def run_ig_scan(adapter: OutboundAdapter | None = None) -> ScanReport | None:
    """Run one IG hashtag scan via the shared pipeline."""
    log_trace("instagram", "Started Instagram hashtag scan")
    last_run = _load_json(LAST_RUN_FILE, {})
    if _already_ran_today(last_run) and "--force" not in sys.argv:
        skill_skipped("ig-scanner", "already ran successfully today")
        log_trace("instagram", "Skipped: already ran today")
        return None
    if not can_act("instagram", "like"):
        skill_skipped("ig-scanner", "Daily IG like limit reached")
        print_status()
        return None

    skill_started("ig-scanner", "Scanning Instagram hashtags for posts to like/comment")
    print_status()

    config = _load_json(CONFIG_FILE, {})
    policy = EngagementPolicy.from_config(config)
    active = adapter or InstagramHashtagAdapter(
        {**config, "session_file": SESSION_FILE, "hashtag_file": HASHTAG_FILE, "headless": False}
    )
    queue_io = _QueueIO(QUEUE_FILE)

    try:
        report = run_outbound_scan(
            active, policy,
            dedup=deduplication, rate_tracker=rate_limiter, drafter=draft_helper,
            queue_io=queue_io, log=log,
            now_iso=lambda: datetime.now(UTC).isoformat(),
            score_relevance=lambda text: _score_relevance(text),
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "SESSION_EXPIRED" in msg or "No saved Instagram session" in msg:
            log_trace("instagram", f"Aborted: {msg}")
            skill_skipped("ig-scanner", msg)
            return None
        raise

    for rec in queue_io.newly_queued:
        deduplication.mark_engaged(
            "instagram", str(rec["post_id"]),
            action="comment_queued",
            group_or_hashtag=str(rec.get("hashtag") or rec.get("group_or_hashtag") or ""),
        )

    last_run["ig_scanner"] = {
        "last_run_at": datetime.now(UTC).isoformat(),
        "hashtags_scanned": report.sources_visited,
        "posts_liked": report.likes_succeeded,
        "posts_queued_for_comment": report.queued,
        "status": "success",
    }
    LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN_FILE.write_text(json.dumps(last_run, indent=2))

    quota = policy.daily_comment_quota.get("instagram", 10)
    skill_finished(
        "ig-scanner",
        f"Hashtags: {report.sources_visited} | "
        f"Liked: {report.likes_succeeded}/8 | Queued: {report.queued}/{quota}",
    )
    print_status()
    return report


if __name__ == "__main__":
    run_ig_scan()
