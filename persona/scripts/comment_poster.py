"""
Comment Poster — posts approved comments from the queue via Playwright.

Only handles items already marked `approved` in the queue. Approval happens
separately in `comment_approver.py`, which runs without a watchdog so the
long Telegram wait doesn't trip the stuck-process detector.

Usage:
    python scripts/comment_poster.py          # post approved items
    python scripts/comment_poster.py --force  # skip re-run guard
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from lib.activity_log import log_trace
from lib.bootstrap import init_script

settings, log = init_script(__name__)

from deduplication import is_duplicate, mark_engaged
from lib.comment_queue_routing import guard_key_for, parse_platform_arg, queue_path_for
from lib.logger import log_progress, log_step
from notifier import (
    skill_finished,
    skill_skipped,
    skill_started,
)
from rate_limiter import can_act, print_status, record_action

# Per-platform loop: `--platform instagram|facebook` drains only that queue;
# absent (or `--platform wordpress`) drains the legacy shared queue.
PLATFORM = parse_platform_arg(sys.argv)
GUARD_KEY = guard_key_for(PLATFORM)

QUEUE_FILE = queue_path_for(PLATFORM)
LAST_RUN_FILE = settings.paths.last_run
LOG_FILE = PROJECT_ROOT / "logs/engagement_log.jsonl"
ERROR_LOG = (settings.paths.logs_dir / "errors.log")


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def log_error(msg: str) -> None:
    ts = datetime.now(UTC).isoformat()
    with ERROR_LOG.open("a") as f:
        f.write(f"[{ts}] {msg}\n")


def log_engagement(action: str, platform: str, target: str, content: str) -> None:
    entry = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now(UTC).isoformat(),
        "action": action,
        "platform": platform,
        "target_name": target,
        "content": content[:200],
    }
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def post_comment_wp(comment_id: str, parent_post_id: int, reply: str) -> tuple[bool, str]:
    """Approve the visitor comment, then post a reply in the persona voice.

    Returns (ok, detail). `detail` is either the reply's public URL on success
    or an error string for the caller to log.

    Two API calls:
      1. POST /wp-json/wp/v2/comments/{id} with {"status": "approved"} —
         publishes the visitor comment from the hold queue.
      2. POST /wp-json/wp/v2/comments with {post, parent, content} — creates
         the Nalla's Dad reply as a child of the visitor comment.

    No Playwright, no browser session — runs from httpx over the same
    application-password auth used by the recipe publisher.
    """
    base = os.environ["WP_URL"].rstrip("/")
    user = os.environ["WP_USER"]
    pw = os.environ["WP_APP_PASSWORD"]
    with httpx.Client(base_url=base, auth=(user, pw), timeout=30.0) as client:
        approve = client.post(
            f"/wp-json/wp/v2/comments/{comment_id}",
            json={"status": "approved"},
        )
        if approve.status_code >= 400:
            return False, f"approve failed: {approve.status_code} {approve.text[:200]}"

        post_reply = client.post(
            "/wp-json/wp/v2/comments",
            json={
                "post": parent_post_id,
                "parent": int(comment_id),
                "content": reply,
            },
        )
        if post_reply.status_code >= 400:
            return False, f"reply failed: {post_reply.status_code} {post_reply.text[:200]}"
        body = post_reply.json()
        return True, body.get("link", "")


def run() -> None:
    print("=== Comment Poster ===\n", flush=True)
    log_trace("system", "Started Comment Poster")

    # Re-run guard (per-platform key so the IG loop never blocks the FB loop)
    last_run = load_json(LAST_RUN_FILE, {})
    cc = last_run.get(GUARD_KEY, {})
    cc_date = (cc.get("last_run_at") or "")[:10]
    if cc_date == date.today().isoformat() and cc.get("status") == "success":
        msg = f"Already ran today — posted {cc.get('comments_posted', 0)}"
        print(f"SKIP: comment-composer already ran today ({cc_date}).", flush=True)
        skill_skipped("comment-composer", msg)
        if "--force" not in sys.argv:
            log_trace("system", "Poster skipped: already ran today")
            return
        print("--force detected, re-running.\n", flush=True)

    # Load queue. When scoped to a platform, act only on that platform's items
    # (a no-op for the per-platform queue files; for the legacy/WordPress queue
    # it prevents touching migrated IG/FB copies that now live in their own queues).
    queue = load_json(QUEUE_FILE, [])
    approved_raw = [
        q for q in queue
        if q.get("status") == "approved" and q.get("draft_comment")
        and (PLATFORM is None or q.get("platform") == PLATFORM)
    ]

    if not approved_raw:
        print("Nothing to post — no approved items in queue.")
        log_trace("system", "Poster finished: nothing to post")
        return

    # Hard guard: drop items whose post_id is already in dedup_cache (was
    # successfully engaged-with on a prior run). Mutates queue so duplicates
    # don't sit pending forever.
    approved = []
    seen_post_ids: set[tuple[str, str]] = set()  # (platform, post_id) within this run
    for item in approved_raw:
        plat = item.get("platform")
        pid = item.get("post_id") or ""
        if plat in ("facebook", "instagram", "wordpress") and pid and is_duplicate(plat, pid):
            item["status"] = "already_engaged"
            item["_blocked_reason"] = "post_id present in dedup_cache before posting"
            continue
        if (plat, pid) in seen_post_ids:
            item["status"] = "duplicate_in_run"
            item["_blocked_reason"] = "another queue item with same post_id approved in same run"
            continue
        seen_post_ids.add((plat, pid))
        approved.append(item)
    save_json(QUEUE_FILE, queue)

    print_status()
    print(f"\nApproved (ready to post): {len(approved)} ({len(approved_raw) - len(approved)} pre-blocked as duplicate)", flush=True)

    if not approved:
        print("Nothing to do — no approved comments in queue.", flush=True)
        return

    skill_started("comment-composer", f"Posting {len(approved)} approved comments")

    # WordPress-only: FB → scripts/fb_comment.py, IG → scripts/ig_comment.py.
    wp_approved = [q for q in approved if q["platform"] == "wordpress"]

    posted = 0
    failed = 0

    # ── WordPress replies (no browser needed — REST API only) ──
    if wp_approved:
        log_step(f"Posting {len(wp_approved)} WordPress replies")
        for idx, item in enumerate(wp_approved):
            if not can_act("wordpress", "reply"):
                print("\nDaily WP reply limit reached.", flush=True)
                break

            comment_id = item["post_id"]
            parent_post_id = int(item["parent_post_id"])
            draft = item["draft_comment"]
            target = item.get("parent_post_title") or f"post {parent_post_id}"
            log_progress(idx + 1, len(wp_approved), f"WP: {target}")

            try:
                ok, detail = post_comment_wp(comment_id, parent_post_id, draft)
                if not ok:
                    print(f"    {detail}", flush=True)
                    log_error(f"WP_COMMENT_FAILED: {comment_id} — {detail}")
                    item["status"] = "POST_FAILED"
                    item["error"] = detail[:200]
                    failed += 1
                    continue

                record_action("wordpress", "reply")
                mark_engaged("wordpress", comment_id, "comment", target)
                log_engagement("comment", "wordpress", target, draft)

                item["status"] = "posted"
                item["posted_at"] = datetime.now(UTC).isoformat() + "Z"
                item["comment_text"] = draft
                item["reply_url"] = detail
                posted += 1
                print(f"    ✅ Posted: {detail}", flush=True)
                save_json(QUEUE_FILE, queue)

                if idx < len(wp_approved) - 1:
                    delay = random.uniform(5, 10)
                    print(f"    Waiting {delay:.0f}s...", flush=True)
                    time.sleep(delay)

            except Exception as e:
                print(f"    ERROR: {e}", flush=True)
                log_error(f"WP_COMMENT_FAILED: {comment_id} — {e}")
                item["status"] = "POST_FAILED"
                item["error"] = str(e)[:200]
                failed += 1

    # WordPress-only now (no browser): FB → fb_comment.py, IG → ig_comment.py.
    save_json(QUEUE_FILE, queue)
    last_run[GUARD_KEY] = {
        "last_run_at": datetime.now(UTC).isoformat(),
        "comments_posted": posted,
        "comments_failed": failed,
        "status": "success",
    }
    save_json(LAST_RUN_FILE, last_run)
    summary = f"📝 Posted: {posted}/{len(approved)} | Failed: {failed}"
    print(f"\n=== Done === {summary}", flush=True)
    skill_finished("comment-composer", summary)


if __name__ == "__main__":
    run()
