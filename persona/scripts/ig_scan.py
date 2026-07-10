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
from lib.task_queue import TaskQueue
from lib.worker_db import record_complete, record_start

settings, log = init_script(__name__)

# Brand-derived so each onboarded brand's worker_runs rows are distinct.
# NOTE: dogfoodandfun's history under the old literal "persona-ig-scanner"
# label is orphaned by this rename — new runs record under the new label.
WORKER_LABEL = f"{settings.paths.brand_dir.name}-ig-scanner"

import deduplication
import rate_limiter
from comment_generator import score_relevance as _score_relevance
from lib.engagement.adapter import OutboundAdapter
from lib.engagement.adapters.instagram import InstagramHashtagAdapter
from lib.engagement.pipeline import ScanReport, run_outbound_scan
from lib.engagement.policy import EngagementPolicy
from lib.engagement.post import Post
from notifier import skill_finished, skill_skipped, skill_started
from rate_limiter import can_act, print_status

LAST_RUN_FILE = settings.paths.last_run
SESSION_FILE = settings.paths.instagram_session
CONFIG_FILE = settings.paths.brand_dir / "config.json"
HASHTAG_FILE = settings.paths.instagram_accounts


class _RedisQueueIO:
    """`run_outbound_scan` queue collaborator: push to Redis TaskQueue."""

    def __init__(self, queue: TaskQueue) -> None:
        self._q = queue
        self.newly_queued: list[dict[str, Any]] = []

    def append(self, record: dict[str, object]) -> None:
        task = dict(record)
        self._q.push(task)
        self.newly_queued.append(task)

    def save(self) -> None:
        pass  # Redis push is immediate; no batch save needed

    def existing_today(self, platform: str) -> int:
        return self._q.depth()


def _load_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


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
    if not can_act("instagram", "like") and "--force" not in sys.argv:
        skill_skipped("ig-scanner", "Daily IG like limit reached")
        print_status()
        return None

    skill_started("ig-scanner", "Scanning Instagram hashtags for posts to like/comment")
    print_status()

    config = _load_json(CONFIG_FILE, {})
    policy = EngagementPolicy.from_config(config)
    active = adapter or InstagramHashtagAdapter(
        {**config, "session_file": SESSION_FILE, "hashtag_file": HASHTAG_FILE}
    )
    queue_io = _RedisQueueIO(TaskQueue("ig-comment"))

    try:
        report = run_outbound_scan(
            active,
            policy,
            # Scan-only: no drafter. Comments are drafted at post time by
            # scripts/ig_comment.py, so the queue holds bare target posts.
            dedup=deduplication,
            rate_tracker=rate_limiter,
            drafter=None,
            queue_io=queue_io,
            log=log,
            now_iso=lambda: datetime.now(UTC).isoformat(),
            score_relevance=_score_post,
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "SESSION_EXPIRED" in msg or "No saved Instagram session" in msg:
            log_trace("instagram", f"Aborted: {msg}")
            skill_skipped("ig-scanner", msg)
            return None
        raise

    from lib.dedup_pg import record_done as _pg_record_done

    for rec in queue_io.newly_queued:
        deduplication.mark_engaged(
            "instagram",
            str(rec["post_id"]),
            action="comment_queued",
            group_or_hashtag=str(rec.get("hashtag") or rec.get("group_or_hashtag") or ""),
        )
        _pg_record_done(
            "scan",
            "instagram",
            str(rec["post_id"]),
            worker_label=WORKER_LABEL,
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


def _health_check() -> int:
    """Verify the IG Playwright session file exists and is non-empty.

    No browser launch, no network call — mirrors
    scripts/fb_group_post.py::_health_check()'s exact pattern (a `> 2` byte
    threshold rejects empty JSON files a torn-down context may write).
    """
    if SESSION_FILE.exists() and SESSION_FILE.stat().st_size > 2:
        print(f"IG session OK (storage: {SESSION_FILE})")
        return 0
    print(f"SESSION_EXPIRED: {SESSION_FILE} missing or empty", file=sys.stderr)
    return 1


if __name__ == "__main__":
    if "--health-check" in sys.argv:
        sys.exit(_health_check())

    _brand_dir = settings.paths.brand_dir
    _brand = _brand_dir.name
    record_start(_brand_dir, WORKER_LABEL, _brand)
    try:
        run_ig_scan()
        record_complete(_brand_dir, WORKER_LABEL, _brand, "success")
    except Exception as _exc:
        record_complete(_brand_dir, WORKER_LABEL, _brand, "error", str(_exc))
        raise
