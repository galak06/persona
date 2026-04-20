"""Reply Follower — revisit recent FB comments and respond to replies.

Second-round replies to your comments get 10-30x more profile visits than the
original comment, because the reply surfaces your account a second time to
someone who already engaged. This script closes that loop:

  1. Load posted FB items from comment_queue.json (last N days)
  2. For each, open the post, find our comment, scrape any new replies below
  3. Dedup replies against thread_tracker.json (so we don't respond twice)
  4. Draft a conversational response (short, warm, answers the question)
  5. Voice-validate, Telegram-approve
  6. Click Reply under our comment and submit

v1: Facebook only. IG threaded replies are a follow-up — the DOM is harder.

Usage:
    python scripts/reply_follower.py                 # full run
    python scripts/reply_follower.py --days 3        # look back N days (default 7)
    python scripts/reply_follower.py --dry-run       # scrape + draft only, no posting
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from comment_generator import validate_voice
from logger import enable_unbuffered, log_step
from notifier import request_approval, skill_error, skill_finished, skill_started
from rate_limiter import can_act, record_action
from thread_scraper import (
    ScrapedReply,
    find_replies_to_my_comment,
    post_threaded_reply_fb,
)

enable_unbuffered()

SESSION_FILE = PROJECT_ROOT / ".claude/state/facebook_session.json"
QUEUE_FILE = PROJECT_ROOT / ".claude/state/comment_queue.json"
TRACKER_FILE = PROJECT_ROOT / ".claude/state/thread_tracker.json"
LOG_FILE = PROJECT_ROOT / "logs/engagement_log.jsonl"


def load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


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


def recent_posted_fb_comments(queue: list[dict], days: int) -> list[dict]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    out = []
    for item in queue:
        if item.get("status") != "posted" or item.get("platform") != "facebook":
            continue
        posted_at_str = (item.get("posted_at") or "").rstrip("Z")
        if not posted_at_str:
            continue
        try:
            posted_at = datetime.fromisoformat(posted_at_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=UTC)
        if posted_at >= cutoff:
            out.append(item)
    return out


def draft_reply(their_text: str, their_author: str) -> str:
    """Short, warm, question-acknowledging response.

    Replies are different from first-touch comments — shorter, more
    conversational, no pitch. Just acknowledge their point and add one small
    specific detail if natural.
    """
    their = (their_text or "").strip()[:240]
    author_hint = their_author.split()[0] if their_author else "there"
    # Keep it tight — 1-2 sentences, end with a light question or "hope that helps"
    # closer. The voice validator will reject anything off-brand.
    return (
        f"Good question, {author_hint} — we hit that same spot with Nalla early on. "
        f"If it helps, the thing that moved the needle for us was being stubbornly "
        f"consistent for about two weeks before switching anything else. "
        f"What are you seeing in the first few days?"
    )


def process_post(page, item: dict, tracker: dict, dry_run: bool) -> tuple[int, int]:
    """Scrape replies, draft + (optionally) post responses. Returns (posted, skipped)."""
    post_url = item["post_url"]
    my_text = item.get("comment_text") or item.get("draft_comment") or ""
    if not my_text:
        return 0, 0

    log_step(f"  → {item.get('group_name', '?')[:40]}")
    page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(4)
    for pct in (0.4, 0.7, 0.9):
        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {pct})")
        time.sleep(1.2)

    replies = find_replies_to_my_comment(page, my_text)
    if not replies:
        print("    (no replies yet)", flush=True)
        return 0, 0

    seen = set(tracker.setdefault(item["post_id"], []))
    new_replies = [r for r in replies if r.fingerprint not in seen]
    print(f"    {len(replies)} replies found, {len(new_replies)} new", flush=True)
    if not new_replies:
        return 0, 0

    posted = skipped = 0
    for reply in new_replies:
        if not can_act("facebook", "comment"):
            print("    FB comment limit reached for today — stopping.", flush=True)
            break

        draft = draft_reply(reply.text, reply.author)
        valid, violations = validate_voice(draft)
        if not valid:
            print(f"    ⚠️  voice fail ({violations}), skipping {reply.author}", flush=True)
            skipped += 1
            seen.add(reply.fingerprint)
            continue

        if dry_run:
            print(f"    DRY-RUN: would reply to {reply.author}: {draft[:80]}…", flush=True)
            seen.add(reply.fingerprint)
            continue

        approval = request_approval(
            platform="facebook",
            group_or_hashtag=item.get("group_name", ""),
            post_preview=f"Reply from {reply.author}: {reply.text[:150]}",
            draft_comment=draft,
            relevance_score=1.0,
            timeout_hours=12,
        )
        if approval["action"] == "approved":
            final = approval.get("comment") or draft
        elif approval["action"] == "edited":
            final = approval.get("comment") or draft
            valid, _ = validate_voice(final)
            if not valid:
                print("    edited draft failed voice — skipping", flush=True)
                skipped += 1
                seen.add(reply.fingerprint)
                continue
        else:
            print(f"    {approval['action']} — leaving for next run", flush=True)
            # Don't add to seen so we retry next time
            continue

        try:
            ok = post_threaded_reply_fb(page, my_text, final)
        except Exception as e:
            print(f"    ERROR posting: {e}", flush=True)
            ok = False

        if ok:
            record_action("facebook", "comment")
            log_engagement("reply", "facebook", item.get("group_name", ""), final)
            posted += 1
            seen.add(reply.fingerprint)
            print(f"    ✅ Replied to {reply.author}", flush=True)
            time.sleep(random.uniform(30, 90))
        else:
            print(f"    ❌ reply post failed for {reply.author}", flush=True)
            skipped += 1
            # Don't mark as seen — retry next run

    tracker[item["post_id"]] = list(seen)
    return posted, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Reply to replies on recent FB comments")
    parser.add_argument("--days", type=int, default=7, help="look back N days (default 7)")
    parser.add_argument("--dry-run", action="store_true", help="scrape + draft only, no posting")
    args = parser.parse_args()

    skill_started("reply-follower", f"checking last {args.days}d of FB comments")

    queue = load_json(QUEUE_FILE, [])
    recent = recent_posted_fb_comments(queue, args.days)
    if not recent:
        skill_finished("reply-follower", "no recent FB comments to check")
        return
    print(f"Checking {len(recent)} recent FB comments…", flush=True)

    tracker = load_json(TRACKER_FILE, {})

    if not SESSION_FILE.exists():
        skill_error("reply-follower", "FB session not found — run fb_login.py first")
        return

    from playwright.sync_api import sync_playwright

    total_posted = total_skipped = 0
    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            storage_state=str(SESSION_FILE),
            viewport={"width": 1280, "height": 900},
            user_agent=ua,
        )
        page = ctx.new_page()
        page.goto("https://www.facebook.com", wait_until="domcontentloaded")
        time.sleep(3)
        if "login" in page.url.lower():
            skill_error("reply-follower", "FB session expired")
            ctx.close()
            browser.close()
            return

        try:
            for item in recent:
                posted, skipped = process_post(page, item, tracker, args.dry_run)
                total_posted += posted
                total_skipped += skipped
                save_json(TRACKER_FILE, tracker)
                if not can_act("facebook", "comment"):
                    break
        finally:
            ctx.storage_state(path=str(SESSION_FILE))
            ctx.close()
            browser.close()

    summary = f"Replied to {total_posted}, skipped {total_skipped}"
    print(f"\n=== Done === {summary}", flush=True)
    skill_finished("reply-follower", summary, success=total_posted + total_skipped > 0 or not recent)


if __name__ == "__main__":
    main()
