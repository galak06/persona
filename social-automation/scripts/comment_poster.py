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

    # Split approved into FB and IG
    fb_approved = [q for q in approved if q["platform"] == "facebook"]
    ig_approved = [q for q in approved if q["platform"] == "instagram"]

    from playwright.sync_api import sync_playwright

    posted = 0
    failed = 0
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
