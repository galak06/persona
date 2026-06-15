"""Instagram Comment — draft a reply at post time and post it.

Single-responsibility counterpart to ``scripts/ig_scan.py``. The scanner only
finds + likes + queues target posts (no draft); this action drains the IG
comment queue: for each pending post (IG queues only questions — '?' posts) it
drafts a reply from the live post text, then submits it via Playwright. No
separate approver. Shares the drain loop with the FB commenter via
``lib.engagement.commenter``.

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

settings, log = init_script(__name__)

from draft_helper import draft_comment_for_post
from lib.comment_queue_routing import guard_key_for
from lib.engagement.commenter import CommenterSpec, main_for
from lib.ig.comment_post import post_comment_ig

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
    queue_file=settings.paths.instagram_comment_queue,
    last_run_file=settings.paths.last_run,
    log_file=settings.paths.logs_dir / "engagement_log.jsonl",
    home_url="https://www.instagram.com",
    login_markers=("login", "accounts/login"),
    target_field="hashtag",
    draft_fn=_draft,
    post_fn=post_comment_ig,
    session_missing_msg="No saved Instagram session — run scripts/ig_login.py first",
)


if __name__ == "__main__":
    sys.exit(main_for(SPEC))
