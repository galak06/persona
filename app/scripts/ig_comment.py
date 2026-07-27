"""Instagram Comment — draft a reply at post time and post it.

RETAINED ONLY TO DRAIN THE PRE-MIGRATION BACKLOG. Instagram moved to a
single-pass flow: ``scripts/ig_scan.py`` now opens each post once and likes
AND comments in that same visit, so nothing writes to the IG comment queue
any more. This script is a consumer without a producer — once the backlog is
drained it has no work, forever, and its daily launchd job would otherwise
stay green while verifying nothing. It therefore skips loudly (naming
``ig_scan.py``) rather than silently reporting success on an empty queue.

Historically the counterpart to ``scripts/ig_scan.py``: the scanner found +
liked + queued target posts (no draft); this action drained the IG comment
queue, drafting a reply from the live post text for each pending post (IG
queued only questions — '?' posts) and submitting it via Playwright. No
separate approver. Shares the drain loop with the FB commenter via
``lib.engagement.commenter``, which Facebook still uses two-stage.

Usage:
    python scripts/ig_comment.py                # draft + post pending IG items
    python scripts/ig_comment.py --dry-run      # draft + print; do not post
    python scripts/ig_comment.py --force        # skip the daily re-run guard
    python scripts/ig_comment.py --limit 3      # cap items handled this run
    python scripts/ig_comment.py --health-check # verify IG session and exit
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.bootstrap import init_script
from lib.worker_db import record_complete, record_start

WORKER_LABEL = "persona-ig-comment"

settings, log = init_script(__name__)

from draft_helper import draft_comment_for_post
from lib.comment_queue_routing import guard_key_for
from lib.engagement.commenter import CommenterSpec, main_for
from lib.ig.comment_post import post_comment_ig
from lib.task_queue import TaskQueue
from notifier import skill_skipped

PLATFORM = "instagram"

if settings.paths is None:
    raise RuntimeError("settings.paths is unset; lib.config failed to resolve BRAND_DIR")


def _draft(item: dict[str, Any]) -> str:
    return draft_comment_for_post(
        platform=PLATFORM,
        post_text=str(item.get("post_text") or ""),
        group_or_hashtag=str(item.get("hashtag") or "") or None,
        post_url=str(item.get("post_url") or "") or None,
    )


SPEC = CommenterSpec(
    platform=PLATFORM,
    skill_name="ig-comment",
    label="IG",
    guard_key=guard_key_for(PLATFORM),
    session_file=settings.paths.instagram_session,
    last_run_file=settings.paths.last_run,
    log_file=settings.paths.logs_dir / "engagement_log.jsonl",
    home_url="https://www.instagram.com",
    login_markers=("login", "accounts/login"),
    target_field="hashtag",
    draft_fn=_draft,
    post_fn=post_comment_ig,
    session_missing_msg="No saved Instagram session — run scripts/ig_login.py first",
    task_queue=TaskQueue("ig-comment"),
)


_NO_PRODUCER_MSG = (
    "IG comment queue is empty and nothing produces into it any more — "
    "Instagram moved to a single-pass flow where scripts/ig_scan.py likes "
    "AND comments in one visit. This script exists only to drain the "
    "pre-migration backlog; the backlog is gone. Retire its scheduled job."
)


def _backlog_is_drained() -> bool:
    """True if the queue is empty, i.e. there is no backlog left to drain.

    Reported explicitly so an empty run is never mistaken for a healthy one.
    Uses the non-destructive ``depth()``; a Redis failure is left to the
    normal drain path to surface rather than being swallowed here.
    """
    if SPEC.task_queue is None:
        return False
    try:
        return SPEC.task_queue.depth() == 0
    except Exception:
        return False


if __name__ == "__main__":
    _brand_dir = settings.paths.brand_dir
    _brand = _brand_dir.name
    if _backlog_is_drained():
        skill_skipped("ig-comment", _NO_PRODUCER_MSG)
        sys.exit(0)
    record_start(_brand_dir, WORKER_LABEL, _brand)
    try:
        _exit_code = main_for(SPEC)
        record_complete(_brand_dir, WORKER_LABEL, _brand, "success")
        sys.exit(_exit_code)
    except Exception as _exc:
        record_complete(_brand_dir, WORKER_LABEL, _brand, "error", str(_exc))
        raise
