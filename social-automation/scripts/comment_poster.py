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
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from logger import enable_unbuffered, log_progress, log_step

enable_unbuffered()

from deduplication import is_duplicate, mark_engaged
from notifier import (
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


def post_comment_wp(comment_id: str, parent_post_id: int, reply: str) -> tuple[bool, str]:
    """Approve the visitor comment, then post a reply as Nalla's Dad.

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


def post_comment_fb(page, post_url: str, comment: str) -> bool:
    """Navigate to FB post and submit comment. Returns True on success."""
    page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(5)

    # Scroll down gradually to trigger lazy-loaded comment section
    for scroll_pct in [0.3, 0.5, 0.7, 0.9]:
        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {scroll_pct})")
        time.sleep(2)

    # Try clicking any "Write a comment" placeholder or comment area first
    # This activates the comment box on modern FB
    clicked_area = page.evaluate("""() => {
        // Method 1: Click placeholder text
        const placeholders = document.querySelectorAll('[role="button"]');
        for (const el of placeholders) {
            const text = (el.textContent || '').trim().toLowerCase();
            if (text.includes('write a comment') || text.includes('comment as')) {
                el.click();
                return 'clicked_placeholder';
            }
        }
        // Method 2: Click any visible comment form area
        const forms = document.querySelectorAll('form[role="presentation"], [data-visualcompletion="ignore-dynamic"]');
        for (const f of forms) {
            const text = (f.textContent || '').toLowerCase();
            if (text.includes('comment') || text.includes('write')) {
                f.click();
                return 'clicked_form';
            }
        }
        return 'none';
    }""")
    print(f"    Pre-click: {clicked_area}", flush=True)
    time.sleep(2)

    # Now find the activated comment box
    found = page.evaluate("""() => {
        // Try multiple selectors in priority order
        const selectors = [
            '[contenteditable="true"][data-lexical-editor="true"]',
            '[contenteditable="true"][role="textbox"]',
            '[contenteditable="true"][aria-label*="comment" i]',
            '[contenteditable="true"][aria-label*="Comment" i]',
            '[contenteditable="true"][aria-label*="Write"]',
            '[contenteditable="true"][aria-placeholder*="comment" i]',
            '[contenteditable="true"][aria-placeholder*="Write" i]',
            'div[contenteditable="true"][spellcheck]',
        ];
        for (const sel of selectors) {
            const box = document.querySelector(sel);
            if (box) {
                box.focus();
                box.click();
                return 'found:' + sel;
            }
        }
        // Last resort: any contenteditable that's not the main post editor
        const allEditable = document.querySelectorAll('[contenteditable="true"]');
        for (const el of allEditable) {
            const rect = el.getBoundingClientRect();
            // Skip tiny or hidden elements
            if (rect.height > 20 && rect.height < 200 && rect.width > 100) {
                el.focus();
                el.click();
                return 'found:contenteditable_fallback';
            }
        }
        return 'not_found';
    }""")
    print(f"    Comment box: {found}", flush=True)

    if not found.startswith("found"):
        # One more try — scroll all the way down
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(3)
        found = page.evaluate("""() => {
            const selectors = [
                '[contenteditable="true"][data-lexical-editor="true"]',
                '[contenteditable="true"][role="textbox"]',
                '[contenteditable="true"][aria-label*="comment" i]',
                'div[contenteditable="true"][spellcheck]',
            ];
            for (const sel of selectors) {
                const box = document.querySelector(sel);
                if (box) { box.focus(); box.click(); return 'found:' + sel; }
            }
            return 'not_found';
        }""")
        print(f"    Comment box (retry): {found}", flush=True)

    if not found.startswith("found"):
        return False

    time.sleep(1)
    page.keyboard.type(comment, delay=30)
    time.sleep(2)

    # Submit — try button first, then Enter
    sub = page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('[role="button"]'));
        const submit = btns.find(b => {
            const label = (b.getAttribute('aria-label') || '').toLowerCase();
            return label === 'comment' || label === 'post' || label === 'submit' || label === 'reply';
        });
        if (submit) { submit.click(); return 'clicked'; }
        return 'not_found';
    }""")
    if sub != "clicked":
        page.keyboard.press("Enter")
    print(f"    Submit: {sub}", flush=True)

    time.sleep(3)
    return True


def post_comment_ig(page, post_url: str, comment: str) -> bool:
    """Navigate to IG post and submit comment. Returns True on success."""
    page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(5)

    # Click the comment input area
    found = page.evaluate("""() => {
        // Try textarea first (IG uses textarea for comments)
        const textarea = document.querySelector('textarea[aria-label*="comment" i]') ||
                         document.querySelector('textarea[placeholder*="comment" i]') ||
                         document.querySelector('textarea[placeholder*="Add a comment" i]');
        if (textarea) { textarea.click(); textarea.focus(); return 'found:textarea'; }

        // Try contenteditable
        const ce = document.querySelector('[contenteditable="true"][role="textbox"]');
        if (ce) { ce.click(); ce.focus(); return 'found:contenteditable'; }

        // Try any form with comment text
        const forms = document.querySelectorAll('form');
        for (const f of forms) {
            const ta = f.querySelector('textarea');
            if (ta) { ta.click(); ta.focus(); return 'found:form_textarea'; }
        }
        return 'not_found';
    }""")
    print(f"    IG comment box: {found}", flush=True)

    if not found.startswith("found"):
        return False

    time.sleep(1)
    page.keyboard.type(comment, delay=30)
    time.sleep(2)

    # Submit — look for Post button
    sub = page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('button, [role="button"], div[tabindex="0"]'));
        const post = btns.find(b => {
            const text = (b.textContent || '').trim().toLowerCase();
            return text === 'post' || text === 'submit';
        });
        if (post) { post.click(); return 'clicked'; }
        return 'not_found';
    }""")
    if sub != "clicked":
        page.keyboard.press("Enter")
    print(f"    IG submit: {sub}", flush=True)

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
    approved_raw = [q for q in queue if q.get("status") == "approved" and q.get("draft_comment")]

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

    # Split approved by platform.
    fb_approved = [q for q in approved if q["platform"] == "facebook"]
    ig_approved = [q for q in approved if q["platform"] == "instagram"]
    wp_approved = [q for q in approved if q["platform"] == "wordpress"]

    posted = 0
    failed = 0

    # ── WordPress replies (no browser needed — REST API only) ──
    if wp_approved:
        log_step(f"Posting {len(wp_approved)} WordPress replies")
        for idx, item in enumerate(wp_approved):
            if not can_act("wordpress", "comment"):
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

                record_action("wordpress", "comment")
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
                    delay = random.uniform(15, 45)
                    print(f"    Waiting {delay:.0f}s...", flush=True)
                    time.sleep(delay)

            except Exception as e:
                print(f"    ERROR: {e}", flush=True)
                log_error(f"WP_COMMENT_FAILED: {comment_id} — {e}")
                item["status"] = "POST_FAILED"
                item["error"] = str(e)[:200]
                failed += 1

    # If everything was WP, skip Playwright launch entirely.
    if not (fb_approved or ig_approved):
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
        return

    from playwright.sync_api import sync_playwright

    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        # ── Facebook comments ──
        if fb_approved and can_act("facebook", "comment"):
            log_step(f"Posting {len(fb_approved)} Facebook comments")
            fb_ctx = browser.new_context(
                storage_state=str(SESSION_FILE),
                viewport={"width": 1280, "height": 900},
                user_agent=ua,
            )
            fb_page = fb_ctx.new_page()

            # Session check
            fb_page.goto("https://www.facebook.com", wait_until="domcontentloaded")
            time.sleep(3)
            if "login" in fb_page.url.lower():
                print("ABORT: Facebook session expired.", flush=True)
                skill_error("comment-composer", "Facebook session expired")
            else:
                log_step("Facebook session OK")
                for idx, item in enumerate(fb_approved):
                    if not can_act("facebook", "comment"):
                        print("\nDaily FB comment limit reached.", flush=True)
                        break

                    group = item.get("group_name", "")
                    draft = item["draft_comment"]
                    log_progress(idx + 1, len(fb_approved), f"FB: {group}")

                    try:
                        ok = post_comment_fb(fb_page, item["post_url"], draft)
                        if not ok:
                            print("    Comment box not found", flush=True)
                            item["status"] = "COMMENT_BOX_NOT_FOUND"
                            mark_engaged("facebook", item["post_id"], "comment", group, status="failed")
                            failed += 1
                            continue

                        record_action("facebook", "comment")
                        mark_engaged("facebook", item["post_id"], "comment", group)
                        log_engagement("comment", "facebook", group, draft)

                        item["status"] = "posted"
                        item["posted_at"] = datetime.now(UTC).isoformat() + "Z"
                        item["comment_text"] = draft
                        posted += 1
                        print("    ✅ Posted!", flush=True)
                        save_json(QUEUE_FILE, queue)

                        if idx < len(fb_approved) - 1:
                            delay = random.uniform(30, 120)
                            print(f"    Waiting {delay:.0f}s...", flush=True)
                            time.sleep(delay)

                    except Exception as e:
                        print(f"    ERROR: {e}", flush=True)
                        log_error(f"COMMENT_POST_FAILED: {group} — {e}")
                        item["status"] = "POST_FAILED"
                        item["error"] = str(e)[:200]
                        mark_engaged("facebook", item["post_id"], "comment", group, status="failed")
                        failed += 1

            fb_ctx.storage_state(path=str(SESSION_FILE))
            fb_ctx.close()

        # ── Instagram comments ──
        if ig_approved and can_act("instagram", "ig_comment"):
            log_step(f"Posting {len(ig_approved)} Instagram comments")
            ig_ctx = browser.new_context(
                storage_state=str(IG_SESSION_FILE),
                viewport={"width": 1280, "height": 900},
                user_agent=ua,
            )
            ig_page = ig_ctx.new_page()

            # Session check
            ig_page.goto("https://www.instagram.com", wait_until="domcontentloaded")
            time.sleep(4)
            if "login" in ig_page.url.lower() or "accounts/login" in ig_page.url.lower():
                print("ABORT: Instagram session expired.", flush=True)
                skill_error("comment-composer", "Instagram session expired")
            else:
                log_step("Instagram session OK")
                for idx, item in enumerate(ig_approved):
                    if not can_act("instagram", "ig_comment"):
                        print("\nDaily IG comment limit reached.", flush=True)
                        break

                    hashtag = item.get("hashtag", "")
                    draft = item["draft_comment"]
                    log_progress(idx + 1, len(ig_approved), f"IG: #{hashtag}")

                    try:
                        ok = post_comment_ig(ig_page, item["post_url"], draft)
                        if not ok:
                            print("    Comment box not found", flush=True)
                            item["status"] = "COMMENT_BOX_NOT_FOUND"
                            mark_engaged("instagram", item["post_id"], "comment", hashtag, status="failed")
                            failed += 1
                            continue

                        record_action("instagram", "ig_comment")
                        mark_engaged("instagram", item["post_id"], "comment", hashtag)
                        log_engagement("comment", "instagram", hashtag, draft)

                        item["status"] = "posted"
                        item["posted_at"] = datetime.now(UTC).isoformat() + "Z"
                        item["comment_text"] = draft
                        posted += 1
                        print("    ✅ Posted!", flush=True)
                        save_json(QUEUE_FILE, queue)

                        if idx < len(ig_approved) - 1:
                            delay = random.uniform(120, 180)
                            print(f"    Waiting {delay:.0f}s...", flush=True)
                            time.sleep(delay)

                    except Exception as e:
                        print(f"    ERROR: {e}", flush=True)
                        log_error(f"IG_COMMENT_FAILED: #{hashtag} — {e}")
                        item["status"] = "POST_FAILED"
                        item["error"] = str(e)[:200]
                        mark_engaged("instagram", item["post_id"], "comment", hashtag, status="failed")
                        failed += 1

            ig_ctx.storage_state(path=str(IG_SESSION_FILE))
            ig_ctx.close()

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
