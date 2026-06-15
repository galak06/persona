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

settings, log = init_script(__name__)

from draft_helper import draft_short_comment_for_post
from lib.comment_queue_routing import guard_key_for
from lib.engagement.commenter import CommenterSpec, main_for
from lib.fb.comment_post import post_comment_fb

PLATFORM = "facebook"

if settings.paths is None:
    raise RuntimeError("settings.paths is unset; lib.config failed to resolve BRAND_DIR")


def _draft(item: dict[str, Any]) -> str:
    return draft_short_comment_for_post(
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
    session_missing_msg="No saved Facebook session — run scripts/fb_login.py first",
)


if __name__ == "__main__":
    sys.exit(main_for(SPEC))
