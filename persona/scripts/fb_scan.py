"""Facebook Group Scanner — thin wrapper around `run_outbound_scan`.

Orchestration lives in `lib.engagement.pipeline`; platform mechanics live in
`FacebookGroupAdapter`. This wrapper builds the collaborators, applies the
48h comment-warmup gate, calls the pipeline, and persists last-run + dedup
marks.
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
from lib.worker_db import record_complete, record_start

settings, log = init_script(__name__)

# Brand-derived so each onboarded brand's worker_runs rows are distinct.
# NOTE: dogfoodandfun's history under the old literal "dogfood-fb-scanner"
# label is orphaned by this rename — new runs record under the new label.
WORKER_LABEL = f"{settings.paths.brand_dir.name}-fb-scanner"

import deduplication
import rate_limiter
from comment_generator import score_relevance as _score_relevance
from group_warmup import COMMENT_WARMUP_HOURS, is_group_warm
from lib.engagement.adapter import OutboundAdapter, Source
from lib.engagement.adapters.facebook import FacebookGroupAdapter
from lib.engagement.pipeline import ScanReport, run_outbound_scan
from lib.engagement.policy import EngagementPolicy
from lib.engagement.post import Post
from notifier import skill_finished, skill_skipped, skill_started
from rate_limiter import can_act, print_status

QUEUE_FILE = settings.paths.facebook_comment_queue
LAST_RUN_FILE = settings.paths.last_run
SESSION_FILE = settings.paths.facebook_session
CONFIG_FILE = settings.paths.brand_dir / "config.json"


class _WarmFiltered:
    """Filter the FB adapter's sources by the 48h comment-warmup gate.

    Delegates every other adapter method to `inner` so the pipeline still
    sees a normal OutboundAdapter. Keeps warmup at the scanner edge —
    adapter stays pure platform-IO; pipeline stays platform-agnostic.
    """

    def __init__(self, inner: OutboundAdapter) -> None:
        self._inner = inner
        self.platform: str = inner.platform

    def list_sources(self) -> list[Source]:
        return [s for s in self._inner.list_sources() if is_group_warm(s.url, COMMENT_WARMUP_HOURS)]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _QueueIO:
    """`run_outbound_scan` queue collaborator: append + atomic save + today-count."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._existing: list[dict[str, Any]] = json.loads(path.read_text()) if path.exists() else []
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
            1
            for q in self._existing
            if q.get("platform") == platform
            and str(q.get("queued_at", "")).startswith(today)
            and q not in self.newly_queued
        )


def _load_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


def _score_post(post: Post) -> float:
    """Adapt the pipeline's `(Post) -> float` callable to the real
    `score_relevance(text, post_meta, group_category)` signature.

    Restores the FB meta signals dropped during the slice-3 pipeline
    extraction. Mirrors the pre-pipeline call in slice 2 (commit 856013e).
    """
    comment_count_raw = post.platform_extra.get("comment_count", 0) or 0
    category_raw = post.platform_extra.get("category", "food") or "food"
    return _score_relevance(
        post.text,
        {"comment_count": int(comment_count_raw), "hours_old": 12},  # type: ignore[call-overload]
        group_category=str(category_raw),
    )


def _already_ran_today(last_run: dict[str, Any]) -> bool:
    fb = last_run.get("fb_scanner", {})
    return (fb.get("last_run_at") or "")[:10] == date.today().isoformat() and fb.get(
        "status"
    ) == "success"


def run_fb_scan(adapter: OutboundAdapter | None = None) -> ScanReport | None:
    """Run one FB group scan via the shared pipeline."""
    log_trace("facebook", "Started Facebook group scan")

    if adapter is None and not SESSION_FILE.exists():
        msg = "No saved Facebook session — run scripts/fb_login.py first"
        log_trace("facebook", f"Aborted: {msg}")
        skill_skipped("fb-scanner", msg)
        return None

    last_run = _load_json(LAST_RUN_FILE, {})
    if _already_ran_today(last_run) and "--force" not in sys.argv:
        skill_skipped("fb-scanner", "already ran successfully today")
        log_trace("facebook", "Skipped: already ran today")
        return None
    if not can_act("facebook", "group_visit") and "--force" not in sys.argv:
        skill_skipped("fb-scanner", "Daily group visit limit reached")
        print_status()
        return None

    skill_started("fb-scanner", "Scanning Facebook dog groups for posts to engage with")
    print_status()

    config = _load_json(CONFIG_FILE, {})
    # Inject resolved paths so the adapter doesn't get empty strings
    config["paths"] = {
        "facebook_session": str(settings.paths.facebook_session),
        "groups_tracker": str(settings.paths.groups_tracker),
    }

    policy = EngagementPolicy.from_config(config)
    active = adapter or FacebookGroupAdapter(config)
    queue_io = _QueueIO(QUEUE_FILE)

    try:
        report = run_outbound_scan(
            _WarmFiltered(active),
            policy,
            # Scan-only: no drafter. Comments are drafted at post time by
            # scripts/fb_comment.py, so the queue holds bare target posts.
            dedup=deduplication,
            rate_tracker=rate_limiter,
            drafter=None,
            queue_io=queue_io,
            log=log,
            now_iso=lambda: datetime.now(UTC).isoformat(),
            score_relevance=_score_post,
        )
    except (RuntimeError, FileNotFoundError) as exc:
        msg = str(exc)
        log_trace("facebook", f"Aborted: {msg}")
        skill_skipped("fb-scanner", msg)
        return None

    # Preserve the legacy "comment_queued" dedup marker on each newly-queued post
    # (pipeline only marks likes). Mirrors pre-pipeline fb_scan semantics.
    for rec in queue_io.newly_queued:
        deduplication.mark_engaged(
            "facebook",
            str(rec["post_id"]),
            action="comment_queued",
            group_or_hashtag=str(rec.get("group_name") or ""),
        )

    # Permanently mark comments-disabled posts so future scans skip them at the
    # dedup gate — FB turned commenting off, so a comment would always fail.
    for post_id, reason in report.pre_filtered_posts:
        if reason == "comments_disabled":
            deduplication.mark_engaged(
                "facebook",
                str(post_id),
                action="comments_disabled",
                group_or_hashtag="",
            )

    last_run["fb_scanner"] = {
        "last_run_at": datetime.now(UTC).isoformat(),
        "groups_scanned": report.sources_visited,
        "posts_queued": report.queued,
        "status": "success",
    }
    LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN_FILE.write_text(json.dumps(last_run, indent=2))

    quota = policy.daily_comment_quota.get("facebook", 5)
    disabled = report.pre_filtered.get("comments_disabled", 0)
    skill_finished(
        "fb-scanner",
        f"Groups: {report.sources_visited} | "
        f"Candidates: {report.candidates} | Queued: {report.queued}/{quota}"
        f" | Comments-off skipped: {disabled}",
    )
    print_status()
    log_trace(
        "facebook",
        f"Scan complete: {report.sources_visited} groups, "
        f"{report.queued} queued, {disabled} comments-off skipped",
    )
    return report


def _health_check() -> int:
    """Verify the FB Playwright session file exists and is non-empty.

    No browser launch, no network call — mirrors
    scripts/fb_group_post.py::_health_check()'s exact pattern (a `> 2` byte
    threshold rejects empty JSON files a torn-down context may write).
    """
    if SESSION_FILE.exists() and SESSION_FILE.stat().st_size > 2:
        print(f"FB session OK (storage: {SESSION_FILE})")
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
        run_fb_scan()
        record_complete(_brand_dir, WORKER_LABEL, _brand, "success")
    except Exception as _exc:
        record_complete(_brand_dir, WORKER_LABEL, _brand, "error", str(_exc))
        raise
