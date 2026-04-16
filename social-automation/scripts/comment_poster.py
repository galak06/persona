"""
Comment Poster — standalone script version of comment-composer skill.
Posts approved comments from the queue via Playwright.
Sends new pending items to Telegram for approval.

Usage:
    python scripts/comment_poster.py          # post approved, request approval for pending
    python scripts/comment_poster.py --force  # skip re-run guard
"""

from __future__ import annotations

import json
import random
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from logger import enable_unbuffered, log_progress, log_step

enable_unbuffered()

from deduplication import mark_engaged
from notifier import (
    request_approval,
    skill_error,
    skill_finished,
    skill_skipped,
    skill_started,
)
from rate_limiter import can_act, print_status, record_action

SESSION_FILE = PROJECT_ROOT / ".claude/state/facebook_session.json"
IG_SESSION_FILE = PROJECT_ROOT / ".claude/state/instagram_session.json"
QUEUE_FILE = PROJECT_ROOT / ".claude/state/comment_queue.json"
LAST_RUN_FILE = PROJECT_ROOT / ".claude/state/last_run.json"
LOG_FILE = PROJECT_ROOT / "logs/engagement_log.jsonl"
ERROR_LOG = PROJECT_ROOT / "logs/errors.log"


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


def build_engagement_history() -> set[str]:
    """Load set of groups/hashtags we've previously posted to."""
    groups: set[str] = set()
    if LOG_FILE.exists():
        for line in LOG_FILE.open():
            try:
                entry = json.loads(line)
                if entry.get("action") in ("comment", "like"):
                    groups.add(entry["target_name"])
            except Exception:
                continue
    return groups


def post_comment_fb(page, post_url: str, comment: str) -> bool:
    """Navigate to FB post and submit comment. Returns True on success."""
    page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(4)

    # Scroll to comments
    page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
    time.sleep(2)

    # Find comment box
    found = page.evaluate("""() => {
        const box = document.querySelector('[contenteditable="true"][data-lexical-editor="true"]') ||
                    document.querySelector('[contenteditable="true"][role="textbox"]') ||
                    document.querySelector('[aria-label*="Write a comment"]');
        if (box) { box.focus(); box.click(); return 'found'; }
        return 'not_found';
    }""")

    if found != "found":
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(3)
        found = page.evaluate("""() => {
            const box = document.querySelector('[contenteditable="true"][data-lexical-editor="true"]') ||
                        document.querySelector('[contenteditable="true"][role="textbox"]') ||
                        document.querySelector('[aria-label*="Write a comment"]');
            if (box) { box.focus(); box.click(); return 'found'; }
            return 'not_found';
        }""")

    if found != "found":
        return False

    time.sleep(1)
    page.keyboard.type(comment, delay=30)
    time.sleep(2)

    # Submit
    sub = page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('[role="button"]'));
        const submit = btns.find(b => {
            const label = (b.getAttribute('aria-label') || '').toLowerCase();
            return label === 'comment' || label === 'post' || label === 'submit';
        });
        if (submit) { submit.click(); return 'clicked'; }
        return 'not_found';
    }""")
    if sub != "clicked":
        page.keyboard.press("Enter")

    time.sleep(3)
    return True


def run() -> None:
    print("=== Comment Poster ===\n", flush=True)

    # Re-run guard
    last_run = load_json(LAST_RUN_FILE, {})
    cc = last_run.get("comment_composer", {})
    cc_date = (cc.get("last_run_at") or "")[:10]
    if cc_date == date.today().isoformat() and cc.get("status") == "success":
        msg = f"Already ran today — posted {cc.get('comments_posted', 0)}"
        print(f"SKIP: comment-composer already ran today ({cc_date}).", flush=True)
        skill_skipped("comment-composer", msg)
        if "--force" not in sys.argv:
            return
        print("--force detected, re-running.\n", flush=True)

    # Load queue
    queue = load_json(QUEUE_FILE, [])
    approved = [q for q in queue if q.get("status") == "approved" and q.get("draft_comment")]
    pending = [q for q in queue if q.get("status") == "pending" and q.get("draft_comment")]

    print_status()
    print(f"\nApproved (ready to post): {len(approved)}", flush=True)
    print(f"Pending (need approval):  {len(pending)}", flush=True)

    if not approved and not pending:
        print("Nothing to do — queue empty.", flush=True)
        return

    # Send pending items to Telegram for approval
    previously_posted = build_engagement_history()

    for item in pending:
        group = item.get("group_name") or item.get("hashtag", "")
        is_new = group not in previously_posted
        needs_approval = (
            item.get("requires_approval", False)
            or item["platform"] == "instagram"
            or "dogfoodandfun.com" in item.get("draft_comment", "").lower()
            or is_new
        )

        if needs_approval:
            log_step(f"Requesting approval: {group}")
            result = request_approval(
                platform=item["platform"],
                group_or_hashtag=group,
                post_preview=item["post_text"][:200],
                draft_comment=item["draft_comment"],
                relevance_score=item["relevance_score"],
                timeout_hours=12,
            )
            if result["action"] == "approved" or result["action"] == "edited":
                item["status"] = "approved"
                item["draft_comment"] = result["comment"]
                approved.append(item)
            elif result["action"] == "pending":
                print("  Telegram unreachable — keeping pending", flush=True)
            else:
                item["status"] = "USER_SKIPPED"
        else:
            item["status"] = "approved"
            approved.append(item)

    save_json(QUEUE_FILE, queue)

    # Refresh approved list
    approved = [q for q in queue if q.get("status") == "approved" and q.get("draft_comment")]

    if not approved:
        print("\nNo approved comments to post.", flush=True)
        return

    skill_started("comment-composer", f"Posting {len(approved)} approved comments")

    # Post approved comments
    from playwright.sync_api import sync_playwright

    posted = 0
    failed = 0

    with sync_playwright() as p:
        log_step("Launching browser")
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            storage_state=str(SESSION_FILE),
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        log_step("Browser launched")

        # Session check
        log_step("Checking Facebook session")
        page.goto("https://www.facebook.com", wait_until="domcontentloaded")
        time.sleep(3)
        if "login" in page.url.lower():
            print("ABORT: Facebook session expired.", flush=True)
            skill_error("comment-composer", "Facebook session expired")
            browser.close()
            return
        log_step("Session OK")

        for idx, item in enumerate(approved):
            platform = item["platform"]
            action = "ig_comment" if platform == "instagram" else "comment"

            if not can_act(platform, action):
                print(f"\nDaily {platform} comment limit reached.", flush=True)
                break

            group = item.get("group_name") or item.get("hashtag", "")
            draft = item["draft_comment"]

            log_progress(idx + 1, len(approved), f"Posting to: {group}")

            try:
                ok = post_comment_fb(page, item["post_url"], draft)
                if not ok:
                    print("    Comment box not found", flush=True)
                    item["status"] = "COMMENT_BOX_NOT_FOUND"
                    failed += 1
                    continue

                record_action(platform, action)
                mark_engaged(platform, item["post_id"], "comment", group)
                log_engagement("comment", platform, group, draft)

                item["status"] = "posted"
                item["posted_at"] = datetime.now(UTC).isoformat() + "Z"
                item["comment_text"] = draft
                posted += 1

                print("    ✅ Posted!", flush=True)
                save_json(QUEUE_FILE, queue)

                if idx < len(approved) - 1:
                    delay = random.uniform(30, 120)
                    print(f"    Waiting {delay:.0f}s...", flush=True)
                    time.sleep(delay)

            except Exception as e:
                print(f"    ERROR: {e}", flush=True)
                log_error(f"COMMENT_POST_FAILED: {group} — {e}")
                item["status"] = "POST_FAILED"
                item["error"] = str(e)[:200]
                failed += 1

        context.storage_state(path=str(SESSION_FILE))
        browser.close()

    # Save final state
    save_json(QUEUE_FILE, queue)

    last_run["comment_composer"] = {
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
