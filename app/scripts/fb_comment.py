"""Facebook Comment — draft a short reply at post time and post it.

Single-responsibility counterpart to ``scripts/fb_scan.py``. The scanner only
finds + queues target posts (no draft); this action drains the FB comment
queue: for each pending item it drafts ONE short (~15-25 word) reply grounded
in the live post text, then submits it via Playwright. No separate approver —
pending items post directly, capped at the daily FB comment quota. The drain
loop is shared with the IG commenter via ``lib.engagement.commenter``.

Usage:
    python scripts/fb_comment.py                # draft + post pending FB items
    python scripts/fb_comment.py --dry-run      # draft + print; do not post
    python scripts/fb_comment.py --force        # skip the daily re-run guard
    python scripts/fb_comment.py --limit 3      # cap items handled this run
    python scripts/fb_comment.py --health-check # verify FB session and exit
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

WORKER_LABEL = "dogfood-fb-comment"

settings, log = init_script(__name__)

import draft_helper
from lib.comment_queue_routing import guard_key_for
from lib.engagement.commenter import CommenterSpec, main_for
from lib.fb.comment_post import post_comment_fb

PLATFORM = "facebook"

if settings.paths is None:
    raise RuntimeError("settings.paths is unset; lib.config failed to resolve BRAND_DIR")


# Bound at import: the system prompt comes from .claude/skills/fb-comment/
# SKILL.md, so a broken or missing skill file (SkillPromptError) aborts the
# run at startup — before any queue item is touched — matching this script's
# abort-loudly doctrine. The per-post USER prompt is built per item.
_DRAFTER = draft_helper.for_skill("fb-comment")


def _draft(item: dict[str, Any]) -> str:
    return _DRAFTER.draft_short_comment_for_post(
        platform=PLATFORM,
        post_text=str(item.get("post_text") or ""),
        group_or_hashtag=str(item.get("group_name") or "") or None,
        post_url=str(item.get("post_url") or "") or None,
    )


SPEC = CommenterSpec(
    platform=PLATFORM,
    skill_name="fb-comment",
    label="FB",
    guard_key=guard_key_for(PLATFORM),
    session_file=settings.paths.facebook_session,
    queue_file=settings.paths.facebook_comment_queue,
    last_run_file=settings.paths.last_run,
    log_file=settings.paths.logs_dir / "engagement_log.jsonl",
    home_url="https://www.facebook.com",
    login_markers=("login",),
    target_field="group_name",
    draft_fn=_draft,
    post_fn=post_comment_fb,
    session_missing_msg="No saved Facebook session — run scripts/login.py fb first",
)


if __name__ == "__main__":
    _brand_dir = settings.paths.brand_dir
    _brand = _brand_dir.name
    record_start(_brand_dir, WORKER_LABEL, _brand)
    try:
        _exit_code = main_for(SPEC)
        record_complete(_brand_dir, WORKER_LABEL, _brand, "success")
        sys.exit(_exit_code)
    except Exception as _exc:
        record_complete(_brand_dir, WORKER_LABEL, _brand, "error", str(_exc))
        raise
