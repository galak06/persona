"""Facebook Group Engager — SINGLE PASS like + comment.

Orchestration lives in `lib.engagement.pipeline`; platform mechanics live
in `FacebookGroupAdapter`. This wrapper builds the collaborators, calls the
pipeline, and persists the last-run stamp.

Each post is opened exactly once: the scan scores it, likes it, and — when
it clears the auto-approve threshold — drafts and posts the comment in that
same visit. There is no Redis queue and no `scripts/fb_comment.py` handoff
for this flow. This differs from the older two-stage `scripts/fb_scan.py`
-> `scripts/fb_comment.py` split (scan-and-queue, then a separate drain
run), which keeps running unchanged alongside this one. Iterate-once is
enforced by `lib.scan_dedup.ScanDedup`, which marks every OPENED post so
the next run skips it.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from lib.activity_log import log_trace
from lib.bootstrap import init_script
from lib.worker_db import record_complete, record_start
from lib.worker_labels import worker_label_for_flow

settings, log = init_script(__name__)

WORKER_LABEL = worker_label_for_flow("fb-engager")

import draft_helper
import rate_limiter
from comment_generator import score_relevance as _score_relevance
from lib.engagement.adapter import OutboundAdapter
from lib.engagement.adapters.facebook import FacebookGroupAdapter
from lib.engagement.pipeline import ScanReport, run_outbound_scan
from lib.engagement.policy import EngagementPolicy
from lib.engagement.post import Post
from lib.io.jsonio import read_json, write_json
from lib.scan_dedup import ScanDedup
from notifier import skill_finished, skill_skipped, skill_started
from rate_limiter import can_act, daily_limit, print_status

LAST_RUN_FILE = settings.paths.last_run
SESSION_FILE = settings.paths.facebook_session
CONFIG_FILE = settings.paths.brand_dir / "config.json"

# Bound at import: the system prompt comes from .claude/skills/fb-engager/
# SKILL.md, so a broken or missing skill file (SkillPromptError) aborts the
# run at startup — before any group is scanned — matching
# scripts/fb_comment.py's abort-loudly pattern. The per-post USER prompt is
# built by the drafter per call.
_DRAFTER = draft_helper.for_skill("fb-engager")


def _score_post(post: Post) -> float:
    """Adapt the pipeline's `(Post) -> float` callable to the real
    `score_relevance(text, post_meta, group_category)` signature.

    Mirrors scripts/fb_scan.py's `_score_post` (same FB meta signals:
    comment_count + group_category), restoring what the slice-3 pipeline
    extraction dropped from the pre-pipeline call in slice 2 (commit
    856013e).
    """
    comment_count_raw = post.platform_extra.get("comment_count", 0) or 0
    category_raw = post.platform_extra.get("category", "food") or "food"
    return _score_relevance(
        post.text,
        {"comment_count": int(comment_count_raw), "hours_old": 12},  # type: ignore[call-overload]
        group_category=str(category_raw),
    )


def _already_ran_today(last_run: dict[str, Any]) -> bool:
    fb = last_run.get("fb_engager", {})
    return (fb.get("last_run_at") or "")[:10] == date.today().isoformat() and fb.get(
        "status"
    ) == "success"


def run_fb_engager_scan(
    adapter: OutboundAdapter | None = None, *, dry_run: bool | None = None
) -> ScanReport | None:
    """Run one FB group scan via the shared pipeline.

    ``dry_run`` defaults to ``"--dry-run" in sys.argv``. A dry run likes
    nothing and posts no comment, and consumes no state (no last-run stamp,
    no dedup marks) — so it can be re-run freely. It DOES still call the
    drafter, so the preview shows the comment each qualifying post would
    have received.
    """
    if dry_run is None:
        dry_run = "--dry-run" in sys.argv
    log_trace("facebook", "Started Facebook group engager scan")

    if adapter is None and not SESSION_FILE.exists():
        msg = "No saved Facebook session — run scripts/login.py fb first"
        log_trace("facebook", f"Aborted: {msg}")
        skill_skipped("fb-engager", msg)
        return None

    last_run: dict[str, Any] = read_json(LAST_RUN_FILE, default={})  # type: ignore[assignment]
    # Both daily guards are live-run concerns: a dry run neither stamps
    # last_run.json nor sends a like, so blocking it would force --force --
    # which ALSO lifts the like rate cap. Preview stays freely re-runnable.
    if not dry_run and _already_ran_today(last_run) and "--force" not in sys.argv:
        skill_skipped("fb-engager", "already ran successfully today")
        log_trace("facebook", "Skipped: already ran today")
        return None
    if not dry_run and not can_act("facebook", "like") and "--force" not in sys.argv:
        skill_skipped("fb-engager", "Daily FB like limit reached")
        print_status()
        return None

    label = "DRY RUN — " if dry_run else ""
    skill_started(
        "fb-engager",
        f"{label}Scanning Facebook groups for posts to like/comment",
    )
    print_status()

    config: dict[str, Any] = read_json(CONFIG_FILE, default={})  # type: ignore[assignment]
    # Inject resolved paths so the adapter doesn't get empty strings.
    config["paths"] = {
        "facebook_session": str(settings.paths.facebook_session),
        "groups_tracker": str(settings.paths.groups_tracker),
    }
    policy = EngagementPolicy.from_config(config)
    active = adapter or FacebookGroupAdapter(config)
    try:
        report = run_outbound_scan(
            active,
            policy,
            # Single pass: the drafter runs INSIDE the scan so a qualifying
            # post is liked and commented in the one visit that opened it.
            # No queue, no fb_comment.py handoff (see module docstring).
            dedup=ScanDedup(WORKER_LABEL, log=log),
            rate_tracker=rate_limiter,
            drafter=_DRAFTER,
            log=log,
            now_iso=lambda: datetime.now(UTC).isoformat(),
            score_relevance=_score_post,
            dry_run=dry_run,
            inline_comment=True,
        )
    except (RuntimeError, FileNotFoundError) as exc:
        msg = str(exc)
        log_trace("facebook", f"Aborted: {msg}")
        skill_skipped("fb-engager", msg)
        return None

    # A dry run consumes no state: no dedup marks (the pipeline suppresses
    # them, so posts stay eligible) and no last-run stamp (the
    # already-ran-today guard is not burned).
    if not dry_run:
        last_run["fb_engager"] = {
            "last_run_at": datetime.now(UTC).isoformat(),
            "groups_scanned": report.sources_visited,
            "posts_liked": report.likes_succeeded,
            "posts_commented": report.comments_posted,
            "comments_declined": report.comments_declined,
            "status": "success",
        }
        write_json(LAST_RUN_FILE, last_run)

    # Report the cap that is actually ENFORCED (rate_limiter reads the
    # generated data/rate_limits.json artifact), not EngagementPolicy's
    # config.json-derived copy — the two drift, and the enforced one is the
    # number `_maybe_comment`'s quota gate consults.
    quota = daily_limit("facebook", "comment")
    if dry_run:
        summary = (
            f"DRY RUN (nothing liked, commented or recorded) | "
            f"Groups: {report.sources_visited} | "
            f"Would like: {report.likes_attempted} | "
            f"Would comment: {report.comments_attempted}/{quota} | "
            f"Agent declined: {report.comments_declined}"
        )
    else:
        summary = (
            f"Groups: {report.sources_visited} | "
            f"Liked: {report.likes_succeeded} | "
            f"Commented: {report.comments_posted}/{quota} | "
            f"Agent declined: {report.comments_declined}"
        )
    skill_finished("fb-engager", summary)
    print_status()
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
        run_fb_engager_scan()
        record_complete(_brand_dir, WORKER_LABEL, _brand, "success")
    except Exception as _exc:
        record_complete(_brand_dir, WORKER_LABEL, _brand, "error", str(_exc))
        raise
