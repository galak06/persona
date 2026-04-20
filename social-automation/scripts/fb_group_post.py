"""Post a WP blog link to eligible FB groups.

Reads data/groups_tracker.json, filters to groups with status=joined, drafts a
Nalla's Dad caption per group (category inferred from group name keywords),
sends each to Telegram for approval, and Playwright-posts approved ones into
the group's composer. Rate limits to 3 group posts per day.

Usage:
    python scripts/fb_group_post.py \\
        --url https://dogfoodandfun.com/peanut-butter-banana-dog-biscuits-nallas-sunday-batch/ \\
        --title "Peanut Butter & Banana Dog Biscuits"
    python scripts/fb_group_post.py ... --only 219924639809303   # one group
    python scripts/fb_group_post.py ... --dry-run                # draft + approve, skip posting
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

enable_unbuffered()

SESSION_FILE = PROJECT_ROOT / ".claude/state/facebook_session.json"
TRACKER_FILE = PROJECT_ROOT / "data/groups_tracker.json"
LOG_FILE = PROJECT_ROOT / "logs/engagement_log.jsonl"

# Daily cap on group posts — conservative to avoid spam flags.
_MAX_GROUP_POSTS_PER_DAY = 3

_RECIPE_KEYS = ("recipe", "homemade", "food", "treat", "pup", "nutrition")
_RUNNING_KEYS = ("running", "canicross", "trail", "gps", "tracker", "walk")


def classify(group_name: str) -> str:
    lo = group_name.lower()
    if any(k in lo for k in _RECIPE_KEYS):
        return "recipe"
    if any(k in lo for k in _RUNNING_KEYS):
        return "running"
    return "general"


def draft_for_recipe(title: str) -> str:
    body = (
        f"Made a batch of {title} with Nalla this weekend and they were gone "
        f"by Monday — she's been demanding them every morning since. "
        f"Full recipe with exact amounts, bake time, and swaps if you don't "
        f"have one of the ingredients on hand."
    )
    closer = "\n\nWhat's your dog's most-requested homemade treat?"
    return body + "\n\nLink in first comment 👇" + closer


def draft_for_running(title: str) -> str:
    body = (
        f"Been testing homemade training treats for trail runs with Nalla — "
        f"{title} held up best so far. Calorie-dense, three ingredients, "
        f"doesn't crumble in a ziplock for a 60-minute run."
    )
    closer = "\n\nWhat do you pocket for your dog on long runs?"
    return body + "\n\nLink in first comment 👇" + closer


def draft_for_general(title: str) -> str:
    body = (
        f"Sharing a quick one from our kitchen — {title}. "
        f"Easy, pantry-friendly, and Nalla actually works for them. "
        f"Posted the full breakdown on our site."
    )
    closer = "\n\nWhat's your go-to treat when you're out of the store-bought ones?"
    return body + "\n\nLink in first comment 👇" + closer


def draft_caption(group: dict, title: str, url: str) -> str:
    """Always returns a 'link in first comment' caption — URL lands as comment."""
    category = classify(group["group_name"])
    if category == "recipe":
        return draft_for_recipe(title)
    if category == "running":
        return draft_for_running(title)
    return draft_for_general(title)


def _is_big_group(group: dict) -> bool:
    raw = (group.get("member_count") or "").strip()
    if raw.lower().endswith("k"):
        try:
            return float(raw[:-1]) >= 50
        except ValueError:
            return False
    try:
        return int(raw.replace(",", "")) >= 50000
    except ValueError:
        return False


def open_composer_and_post(page, group_url: str, text: str, link_url: str | None) -> bool:
    """Open the group, find the composer, type, submit. Optionally comment the link."""
    page.goto(group_url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(5)
    # Click the top-of-feed group composer placeholder. Critical: must NOT be
    # inside a [role="article"] — that filter rules out the "Write something"
    # comment-box placeholders that live inside existing posts further down
    # the feed (previous diagnostic showed we were clicking one of those).
    clicked = page.evaluate(
        """() => {
        const nodes = Array.from(document.querySelectorAll('[role="button"], div, span'));
        const placeholder = nodes.find(el => {
            if (el.closest('[role="article"]')) return false;  // skip comment boxes
            const t = (el.textContent || '').trim().toLowerCase();
            return t === 'write something…' || t.startsWith('write something') ||
                   t === 'create a public post…' || t.startsWith('create a public post') ||
                   t.startsWith('write a post');
        });
        if (placeholder) {
            placeholder.scrollIntoView({block: 'center'});
            placeholder.click();
            return 'clicked';
        }
        return 'not_found';
    }"""
    )
    print(f"    composer-open: {clicked}", flush=True)
    time.sleep(3)

    found = page.evaluate(
        """() => {
        const sels = [
            '[contenteditable="true"][data-lexical-editor="true"]',
            '[contenteditable="true"][role="textbox"]',
            '[contenteditable="true"][aria-label*="write" i]',
            '[contenteditable="true"][aria-label*="post" i]',
            '[contenteditable="true"][aria-placeholder*="write" i]',
        ];
        for (const s of sels) {
            const box = document.querySelector(s);
            if (box) { box.focus(); box.click(); return 'found:' + s; }
        }
        return 'not_found';
    }"""
    )
    print(f"    composer-box: {found}", flush=True)
    if not found.startswith("found"):
        return False

    time.sleep(1)
    page.keyboard.type(text, delay=25)
    time.sleep(2)

    submitted = page.evaluate(
        """() => {
        const btns = Array.from(document.querySelectorAll('[role="button"], button'));
        const ACCEPT = ['post', 'publish', 'share', 'submit', 'submit for approval', 'send'];
        const btn = btns.find(b => {
            if (b.getAttribute('aria-disabled') === 'true') return false;
            const l = (b.getAttribute('aria-label') || '').trim().toLowerCase();
            const t = (b.textContent || '').trim().toLowerCase();
            return ACCEPT.includes(l) || ACCEPT.includes(t);
        });
        if (btn) { btn.click(); return 'clicked'; }
        // Fallback: find a button inside the open dialog footer
        const dialog = document.querySelector('[role="dialog"]');
        if (dialog) {
            const dbtns = Array.from(dialog.querySelectorAll('[role="button"], button'));
            const db = dbtns.reverse().find(b => b.getAttribute('aria-disabled') !== 'true');
            if (db) { db.click(); return 'clicked:dialog-fallback'; }
        }
        return 'not_found';
    }"""
    )
    print(f"    submit: {submitted}", flush=True)
    if submitted != "clicked":
        return False

    time.sleep(6)

    # If link_url provided, post it as the first comment under our post.
    if link_url:
        _post_first_comment_link(page, link_url)

    return True


def _post_first_comment_link(page, url: str) -> None:
    """For big groups we drop the link in a first comment (algorithmically favored)."""
    # After posting, FB sometimes navigates to the post detail. Wait + find the
    # newest comment box near the top of the feed.
    time.sleep(3)
    # A simple approach: scroll back to top and target the first comment box.
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(2)
    box = page.evaluate(
        """() => {
        const sel = '[contenteditable="true"][aria-label*="comment" i], ' +
                    '[contenteditable="true"][aria-placeholder*="comment" i]';
        const box = document.querySelector(sel);
        if (box) { box.focus(); box.click(); return 'found'; }
        return 'not_found';
    }"""
    )
    print(f"    link-comment-box: {box}", flush=True)
    if box != "found":
        return
    time.sleep(1)
    page.keyboard.type(url, delay=25)
    time.sleep(1.5)
    page.keyboard.press("Enter")
    time.sleep(3)


def log_engagement(group: dict, text: str) -> None:
    entry = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now(UTC).isoformat(),
        "action": "group_post",
        "platform": "facebook",
        "target_name": group["group_name"],
        "target_url": group["group_url"],
        "content": text[:200],
    }
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Post a WP blog link to joined FB groups")
    parser.add_argument("--url", required=True, help="Blog post URL to share")
    parser.add_argument("--title", required=True, help="Blog post title (for the caption)")
    parser.add_argument("--only", help="group id (digits from URL) — limit to one group")
    parser.add_argument("--dry-run", action="store_true", help="draft + approve, skip posting")
    parser.add_argument(
        "--no-comment",
        action="store_true",
        help="skip the first-comment URL step (the auto-comment step is fragile — use when you'll add the URL manually)",
    )
    args = parser.parse_args()

    skill_started("fb-group-post", f"sharing {args.title[:40]}")

    tracker = json.loads(TRACKER_FILE.read_text())
    joined = [g for g in tracker if g.get("status") == "joined"]

    # Skip groups that aren't post-able: only attempt posting_mode=direct
    # (admins_only/blocked would just waste a Telegram approval cycle).
    direct = [g for g in joined if g.get("posting_mode") == "direct"]

    # Skip groups we already posted to in the last hour — avoids duplicating
    # the post across back-to-back runs on the same day.
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    fresh = []
    for g in direct:
        last = g.get("last_post_at")
        if last:
            try:
                when = datetime.fromisoformat(last.replace("Z", "+00:00"))
                if when >= cutoff:
                    print(f"  ⏭  {g['group_name'][:45]} — posted <1h ago, skipping", flush=True)
                    continue
            except ValueError:
                pass
        fresh.append(g)

    if args.only:
        fresh = [g for g in fresh if args.only in g.get("group_url", "")]
    print(f"Eligible groups: {len(fresh)}", flush=True)
    joined = fresh

    posted = skipped = 0
    with _browser_session() as page:
        for group in joined:
            if posted >= _MAX_GROUP_POSTS_PER_DAY:
                print(f"  ⏹  daily cap reached ({_MAX_GROUP_POSTS_PER_DAY})", flush=True)
                break

            caption = draft_caption(group, args.title, args.url)
            if not caption:
                print(f"  ⏭  {group['group_name']} — no matching template (running-only?)", flush=True)
                continue

            valid, violations = validate_voice(caption)
            if not valid:
                print(f"  ⚠️  {group['group_name']}: voice fail {violations}", flush=True)
                skipped += 1
                continue

            # Auto-comment step is fragile — it sometimes targets the wrong
            # composer and creates a junk duplicate post. Honor --no-comment
            # so caller can paste the URL manually on each landed post.
            link_for_comment = None if args.no_comment else args.url

            log_step(f"  → {group['group_name']} (~{group.get('member_count') or '?'})")

            approval = request_approval(
                platform="facebook",
                group_or_hashtag=group["group_name"],
                post_preview=args.url,
                draft_comment=caption,
                relevance_score=1.0,
                timeout_hours=12,
            )
            if approval["action"] not in ("approved", "edited"):
                print(f"    {approval['action']}", flush=True)
                skipped += 1
                continue
            final = approval.get("comment") or caption

            if args.dry_run:
                print(f"    DRY-RUN: would post {len(final)} chars", flush=True)
                continue

            try:
                ok = open_composer_and_post(page, group["group_url"], final, link_for_comment)
            except Exception as e:
                print(f"    ERROR: {e}", flush=True)
                ok = False

            if ok:
                log_engagement(group, final)
                now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                group["last_post_at"] = now
                # Default to posted; caller can flip to pending_approval via
                # fb_group_note.py if FB shows the "submitted for review" banner.
                group["last_post_status"] = "posted"
                group["last_post_caption"] = final
                TRACKER_FILE.write_text(json.dumps(tracker, indent=2))
                record_action("facebook", "group_post")
                posted += 1
                print("    ✅ Posted", flush=True)
                time.sleep(random.uniform(60, 180))
            else:
                skipped += 1
                print("    ❌ post failed", flush=True)

    summary = f"posted={posted} skipped={skipped}"
    skill_finished("fb-group-post", summary)
    print(f"\n=== Done === {summary}", flush=True)


class _browser_session:
    """Context-manager wrapper around Playwright for a persistent FB page."""

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        if not SESSION_FILE.exists():
            raise RuntimeError("FB session missing — run fb_login.py first")
        # Keep the context-manager object so __exit__ can call it.
        self._pw_cm = sync_playwright()
        pw = self._pw_cm.__enter__()
        ua = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        self._browser = pw.chromium.launch(headless=False)
        self._ctx = self._browser.new_context(
            storage_state=str(SESSION_FILE),
            viewport={"width": 1280, "height": 900},
            user_agent=ua,
        )
        self._page = self._ctx.new_page()
        self._page.goto("https://www.facebook.com", wait_until="domcontentloaded")
        time.sleep(3)
        if "login" in self._page.url.lower():
            raise RuntimeError("FB session expired")
        return self._page

    def __exit__(self, *a):
        try:
            self._ctx.storage_state(path=str(SESSION_FILE))
            self._ctx.close()
            self._browser.close()
        finally:
            self._pw_cm.__exit__(*a)


if __name__ == "__main__":
    main()
