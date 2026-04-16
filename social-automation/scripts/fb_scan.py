"""
Facebook Group Scanner — CLI version using Playwright.
Uses saved session state (from fb_login.py) to browse Facebook groups.
Extracts posts, scores relevance, and queues qualifying posts.

Usage:
    1. First time: python scripts/fb_login.py   (log in, save session)
    2. Then:       python scripts/fb_scan.py     (scan groups)
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

# Ensure lib is importable
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

# Force unbuffered output so watchdog/monitors can see progress
from logger import enable_unbuffered, log_progress, log_step, StepTimer
enable_unbuffered()

from comment_generator import score_relevance
from deduplication import is_duplicate
from notifier import skill_started, skill_finished, skill_error, skill_skipped
from rate_limiter import can_act, print_status, record_action, wait_random_delay

TRACKER_PATH = PROJECT_ROOT / "../../facebook_groups_tracker.xlsx"
QUEUE_FILE = PROJECT_ROOT / ".claude/state/comment_queue.json"
LAST_RUN_FILE = PROJECT_ROOT / ".claude/state/last_run.json"
SESSION_FILE = PROJECT_ROOT / ".claude/state/facebook_session.json"
ERROR_LOG = PROJECT_ROOT / "logs/errors.log"
CONFIG_FILE = PROJECT_ROOT / "config.json"

CATEGORY_MAP = {
    "\U0001f356": "food",   # 🍖
    "\U0001f3c3": "gps",    # 🏃
    "\U0001f3e5": "health",  # 🏥
    "\U0001f3be": "training",  # 🎾
    "\U0001f43e": "general",  # 🐾
}


def load_config() -> dict:
    with CONFIG_FILE.open() as f:
        return json.load(f)


def load_groups() -> list[dict]:
    """Load joined groups from Excel tracker."""
    import pandas as pd

    tracker = TRACKER_PATH
    if not tracker.exists():
        from glob import glob
        hits = glob(
            str(PROJECT_ROOT / "../../**/facebook_groups_tracker.xlsx"),
            recursive=True,
        )
        if hits:
            tracker = Path(hits[0])
        else:
            raise FileNotFoundError("facebook_groups_tracker.xlsx not found")

    df = pd.read_excel(tracker, sheet_name="Groups Database")
    df.columns = [c.replace("\n", " ") for c in df.columns]

    joined = df[
        (df["Joined?"].astype(str).str.contains("Joined", na=False))
        & (~df["Self-Promo Allowed?"].astype(str).str.contains("No", na=False))
    ].dropna(subset=["Facebook URL"])

    groups = []
    for _, row in joined.iterrows():
        url = str(row["Facebook URL"]).strip()
        if "/groups/search" in url:
            continue
        groups.append({
            "name": str(row["Group Name"]),
            "url": url,
            "category": str(row.get("Category", "")),
        })
    return groups


def detect_category(group_category: str) -> str:
    for emoji, cat in CATEGORY_MAP.items():
        if emoji in group_category:
            return cat
    return "food"


def log_error(msg: str) -> None:
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with ERROR_LOG.open("a") as f:
        f.write(f"[{ts}] {msg}\n")


def extract_post_id(url: str) -> str:
    """Extract post ID from Facebook URL."""
    for segment in ["posts/", "permalink/"]:
        if segment in url:
            part = url.split(segment)[-1]
            return part.split("/")[0].split("?")[0]
    return url.split("/")[-1].split("?")[0]


def load_queue() -> list[dict]:
    if QUEUE_FILE.exists():
        with QUEUE_FILE.open() as f:
            return json.load(f)
    return []


def save_queue(queue: list[dict]) -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_FILE.open("w") as f:
        json.dump(queue, f, indent=2)


def load_last_run() -> dict:
    if LAST_RUN_FILE.exists():
        with LAST_RUN_FILE.open() as f:
            return json.load(f)
    return {}


def save_last_run(data: dict) -> None:
    LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LAST_RUN_FILE.open("w") as f:
        json.dump(data, f, indent=2)


# --- Post extraction JS ---
# Uses two proven selectors from open-source scrapers:
#   1. data-ad-rendering-role="story_message" (facebook-group-scraper)
#   2. div[dir="auto"] fallback (Facebook-Scraper)

EXTRACT_POSTS_JS = """
() => {
    const posts = [];
    const seen = new Set();

    // Helper: extract a post URL from a container
    function findPostUrl(container) {
        const links = container.querySelectorAll('a[href]');
        // Direct post links
        for (const a of links) {
            const href = a.href || '';
            if (href.includes('/posts/') || href.includes('/permalink/')) {
                return href.split('?')[0];
            }
        }
        // Group post pattern: /groups/ID/NUMBER
        for (const a of links) {
            const href = a.href || '';
            if (href.match(/\\/groups\\/[^/]+\\/\\d{5,}/)) {
                return href.split('?')[0];
            }
        }
        // Timestamp links (e.g. "2h", "Apr 14") — these link to the post
        for (const a of links) {
            const text = (a.innerText || '').trim();
            const href = a.href || '';
            if (href.includes('/groups/') && href.match(/\\/\\d{5,}/) &&
                (text.match(/^\\d+[hmd]$/i) ||
                 text.match(/^(Yesterday|Just now|\\d+ min)/i) ||
                 text.match(/^[A-Z][a-z]{2,8} \\d/))) {
                return href.split('?')[0];
            }
        }
        return '';
    }

    // Helper: get comment count
    function getCommentCount(container) {
        const all = container.querySelectorAll('span, [aria-label]');
        for (const el of all) {
            const t = el.innerText || el.getAttribute('aria-label') || '';
            const m = t.match(/(\\d+)\\s*comment/i);
            if (m) return parseInt(m[1], 10);
        }
        return 0;
    }

    // Strategy: walk the feed looking for story_message divs
    // These ONLY appear on top-level posts, never on comments
    const storyMsgs = document.querySelectorAll(
        'div[data-ad-rendering-role="story_message"]'
    );

    storyMsgs.forEach(msgEl => {
        try {
            const text = msgEl.innerText?.trim();
            if (!text || text.length < 15) return;

            const key = text.substring(0, 100);
            if (seen.has(key)) return;
            seen.add(key);

            // Walk up to the post container to find URL + metadata
            // Go up several levels to find the post wrapper
            let container = msgEl;
            for (let i = 0; i < 15; i++) {
                container = container.parentElement;
                if (!container) break;
                // Stop at the outermost article
                if (container.getAttribute('role') === 'article') break;
                // Or at a pagelet boundary
                if (container.dataset?.pagelet) break;
            }
            if (!container) container = msgEl.parentElement;

            posts.push({
                text: text.substring(0, 800),
                url: findPostUrl(container),
                comment_count: getCommentCount(container),
                timestamp: '',
            });
        } catch(e) {}
    });

    // Fallback: if story_message found nothing, try top-level articles
    if (posts.length === 0) {
        const articles = document.querySelectorAll('[role="feed"] > div [role="article"]');
        articles.forEach(article => {
            try {
                // Only top-level: skip if this article is inside another article
                const parent = article.parentElement?.closest('[role="article"]');
                if (parent) return;

                const textEls = article.querySelectorAll('[dir="auto"]');
                let text = '';
                for (const el of textEls) {
                    const t = el.innerText?.trim();
                    if (t && t.length > 15) { text = t; break; }
                }
                if (!text) return;

                const key = text.substring(0, 100);
                if (seen.has(key)) return;
                seen.add(key);

                posts.push({
                    text: text.substring(0, 800),
                    url: findPostUrl(article),
                    comment_count: getCommentCount(article),
                    timestamp: '',
                });
            } catch(e) {}
        });
    }

    return posts.slice(0, 20);
}
"""


def dismiss_overlays(page) -> None:
    """Dismiss group welcome popups, login prompts, and other overlays."""
    selectors = [
        # Close button (X) on dialogs
        "[aria-label='Close']",
        "[aria-label='close']",
        # Generic dialog close buttons
        "div[role='dialog'] div[aria-label='Close']",
        "div[role='dialog'] [role='button']:has-text('Not Now')",
        "div[role='dialog'] [role='button']:has-text('OK')",
        "div[role='dialog'] [role='button']:has-text('Got it')",
        "div[role='dialog'] [role='button']:has-text('Skip')",
        # "Save login info" dialog
        "div[role='button']:has-text('Not Now')",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel)
            if btn.count() > 0:
                btn.first.click(timeout=1000)
                time.sleep(1)
                return
        except Exception:
            pass

    # Fallback: press Escape to close any modal
    try:
        page.keyboard.press("Escape")
        time.sleep(1)
    except Exception:
        pass


def click_see_more(page) -> None:
    """Click 'See more' buttons to expand truncated posts."""
    see_more = page.locator("div[role='button']:has-text('See more')")
    count = see_more.count()
    for i in range(min(count, 10)):
        try:
            see_more.nth(i).click(timeout=500)
        except Exception:
            pass
    if count > 0:
        time.sleep(1)


def run_scan() -> None:
    """Main scan entry point."""
    from playwright.sync_api import sync_playwright

    print("=== Facebook Group Scanner (CLI) ===\n", flush=True)

    # Check session file
    if not SESSION_FILE.exists():
        print("ERROR: No saved Facebook session found.")
        print("Run this first:  python scripts/fb_login.py")
        return

    # Already ran successfully today?
    last_run = load_last_run()
    fb_last = last_run.get("fb_scanner", {})
    fb_last_date = (fb_last.get("last_run_at") or "")[:10]
    if fb_last_date == date.today().isoformat() and fb_last.get("status") == "success":
        msg = f"Already ran today — scanned {fb_last.get('groups_scanned', 0)} groups, queued {fb_last.get('posts_queued', 0)} posts"
        print(f"SKIP: fb_scanner already ran successfully today ({fb_last_date}).")
        print("      Use --force to run again anyway.")
        skill_skipped("fb-scanner", msg)
        import sys
        if "--force" not in sys.argv:
            return

    # Pre-flight: rate limits
    if not can_act("facebook", "group_visit"):
        print("ABORT: Daily group visit limit reached. Try again tomorrow.")
        skill_skipped("fb-scanner", "Daily group visit limit reached")
        print_status()
        return

    skill_started("fb-scanner", "Scanning Facebook dog groups for posts to engage with")

    print_status()

    # Load groups
    groups = load_groups()
    print(f"Groups to scan: {len(groups)}")
    for g in groups:
        print(f"  - {g['name']}")
    print()

    # Load existing queue and last run
    queue = load_queue()
    last_run = load_last_run()
    config = load_config()

    relevance_threshold = config["content_analysis"]["relevance_threshold"]
    approval_threshold = config["content_analysis"]["approval_threshold"]

    # Stats
    groups_scanned = 0
    posts_evaluated = 0
    posts_queued = 0
    posts_skipped_dedup = 0
    posts_skipped_score = 0
    high_confidence = 0
    needs_approval = 0

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
        log_step("Browser launched OK")

        # Quick login check
        log_step("Checking Facebook session")
        page.goto("https://www.facebook.com", wait_until="domcontentloaded")
        time.sleep(3)

        if "login" in page.url.lower():
            print("ABORT: Facebook session expired.")
            print("Re-run:  python scripts/fb_login.py")
            log_error("SESSION_EXPIRED: Facebook login required")
            browser.close()
            return

        log_step("Facebook session OK")

        # Switch to Page profile (DogFoodAndFun) for group interactions
        print("Switching to Page profile...")
        page.goto(
            "https://www.facebook.com/pages/?category=your_pages",
            wait_until="domcontentloaded",
        )
        time.sleep(3)

        # Click the page profile switcher
        log_step("Switching to Page profile")
        page_config = config.get("social_channels", {}).get("facebook", {})
        page_name = page_config.get("page_name", "DogFoodAndFun")
        switched = False
        try:
            # Method 1: Look for "Switch Now" or page name link
            switch_btn = page.locator(
                f"a:has-text('{page_name}'), "
                f"div[role='button']:has-text('Switch')"
            )
            if switch_btn.count() > 0:
                switch_btn.first.click(timeout=5000)
                time.sleep(3)
                switched = True
                print(f"  Switched to Page: {page_name}")
        except Exception:
            pass

        if not switched:
            # Method 2: Use the profile switcher in account menu
            try:
                # Click account/profile menu (top-right)
                menu = page.locator(
                    "[aria-label='Your profile'], "
                    "[aria-label='Account'], "
                    "[aria-label='Account controls and settings']"
                )
                if menu.count() > 0:
                    menu.first.click(timeout=3000)
                    time.sleep(2)
                    # Look for "See all profiles" or the page name
                    profiles = page.locator(
                        f"div[role='menuitem']:has-text('{page_name}'), "
                        f"span:has-text('{page_name}')"
                    )
                    if profiles.count() > 0:
                        profiles.first.click(timeout=3000)
                        time.sleep(3)
                        switched = True
                        print(f"  Switched to Page: {page_name}")
                    else:
                        see_all = page.locator(
                            "div[role='menuitem']:has-text('See all profiles')"
                        )
                        if see_all.count() > 0:
                            see_all.first.click(timeout=3000)
                            time.sleep(2)
                            pg = page.locator(f"span:has-text('{page_name}')")
                            if pg.count() > 0:
                                pg.first.click(timeout=3000)
                                time.sleep(3)
                                switched = True
                                print(f"  Switched to Page: {page_name}")
            except Exception:
                pass

        if not switched:
            print(f"  WARNING: Could not switch to Page profile '{page_name}'.")
            print("  Continuing as personal profile.\n")
        else:
            print()

        for group_idx, group in enumerate(groups, 1):
            # Check rate limit before each visit
            if not can_act("facebook", "group_visit"):
                print(f"\nRate limit hit — stopping after {groups_scanned} groups.", flush=True)
                break

            log_progress(group_idx, len(groups), f"Scanning: {group['name']}")
            print(f"    URL: {group['url']}", flush=True)

            visit_recorded = False
            try:
                # Navigate with crash recovery — if context dies, recreate it
                try:
                    page.goto(group["url"], wait_until="domcontentloaded", timeout=30000)
                    time.sleep(4)
                except Exception as nav_err:
                    err_str = str(nav_err).lower()
                    if "target page" in err_str or "context" in err_str or "closed" in err_str:
                        print(f"    Browser context crashed — recreating...")
                        log_error(f"CONTEXT_CRASH: {group['name']} — {nav_err}")
                        try:
                            context.close()
                        except Exception:
                            pass
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
                        time.sleep(2)
                        page.goto(group["url"], wait_until="domcontentloaded", timeout=30000)
                        time.sleep(4)
                    else:
                        raise

                # Dismiss welcome popups and overlays
                dismiss_overlays(page)

                # Scroll to load posts (5 scrolls with pause — more content)
                print("    Scrolling to load posts...", flush=True)
                for i in range(5):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(2)
                    dismiss_overlays(page)

                # Expand truncated posts
                click_see_more(page)
                print("    Extracting posts...", flush=True)

                # Record the group visit BEFORE extraction so it's always counted
                try:
                    record_action("facebook", "group_visit")
                    visit_recorded = True
                except RuntimeError as re:
                    print(f"    Rate limit hit: {re}")
                    break
                except Exception as re:
                    log_error(f"record_action failed for {group['name']}: {re}")
                groups_scanned += 1

                # Debug: count story_message elements before running full JS
                story_count = page.evaluate(
                    "() => document.querySelectorAll('[data-ad-rendering-role=\"story_message\"]').length"
                )
                article_count = page.evaluate(
                    "() => document.querySelectorAll('[role=\"article\"]').length"
                )
                print(f"    story_message elements: {story_count} | articles: {article_count}")

                # Extract posts
                posts = page.evaluate(EXTRACT_POSTS_JS)
                print(f"    Posts extracted: {len(posts)}")

                if not posts:
                    # Last resort: scrape visible text for scoring
                    body_text = page.inner_text("body")
                    print(f"    JS extraction empty (body: {len(body_text)} chars) — trying text fallback")
                    if len(body_text) > 500:
                        # Split into paragraphs and treat each as a potential post
                        paragraphs = [p.strip() for p in body_text.split("\n") if len(p.strip()) > 50]
                        posts = [{"text": p, "url": "", "comment_count": 0} for p in paragraphs[:15]]
                        print(f"    Text fallback: {len(posts)} paragraphs")
                    if not posts:
                        print("    Skipping group (no content extracted).")
                        continue

                category = detect_category(group["category"])

                for post in posts:
                    posts_evaluated += 1
                    post_text = post.get("text", "")
                    post_url = post.get("url", "")
                    comment_count = post.get("comment_count", 0)

                    # Show post snippet for debugging
                    snippet = post_text[:80].replace("\n", " ")
                    has_url = bool(post_url)
                    print(f"    [{posts_evaluated}] {snippet}...")
                    print(f"        url={'yes' if has_url else 'NO'} comments={comment_count}")

                    # Generate a fallback post ID from text hash if no URL
                    if post_url:
                        post_id = extract_post_id(post_url)
                    else:
                        # Use text hash as ID — allows scoring posts without URLs
                        import hashlib
                        post_id = hashlib.md5(
                            post_text[:200].encode()
                        ).hexdigest()[:16]
                        post_url = group["url"]  # use group URL as fallback

                    if not post_id:
                        print("        SKIP: no post ID")
                        continue

                    # Dedup check
                    if is_duplicate("facebook", post_id):
                        posts_skipped_dedup += 1
                        print("        SKIP: already engaged")
                        continue

                    # Score
                    meta = {"comment_count": comment_count, "hours_old": 12}
                    score = score_relevance(post_text, meta, group_category=category)
                    print(f"        score={score} (threshold={relevance_threshold})")

                    if score < relevance_threshold:
                        posts_skipped_score += 1
                        continue

                    # Queue it
                    requires_approval = score < approval_threshold
                    queue.append({
                        "platform": "facebook",
                        "post_url": post_url,
                        "post_id": post_id,
                        "post_text": post_text[:600],
                        "group_name": group["name"],
                        "group_url": group["url"],
                        "category": category,
                        "relevance_score": score,
                        "queued_at": datetime.now(timezone.utc).isoformat(),
                        "status": "pending",
                        "requires_approval": requires_approval,
                    })
                    posts_queued += 1
                    if requires_approval:
                        needs_approval += 1
                    else:
                        high_confidence += 1

                    label = "APPROVAL" if requires_approval else "AUTO"
                    print(
                        f"    QUEUED [{label}] "
                        f"score={score} id={post_id[:20]}"
                    )

            except Exception as e:
                msg = f"Error scanning {group['name']}: {e}"
                print(f"    ERROR: {e}")
                log_error(msg)
                continue

            # Delay between group visits (skip after last group)
            if can_act("facebook", "group_visit"):
                wait_random_delay("facebook", "group_visit")

        # Save updated session state (refreshed cookies)
        context.storage_state(path=str(SESSION_FILE))
        browser.close()

    # Save queue
    save_queue(queue)

    # Update last run — mark success so re-run guard works
    last_run["fb_scanner"] = {
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "groups_scanned": groups_scanned,
        "posts_queued": posts_queued,
        "status": "success",
    }
    save_last_run(last_run)

    # Summary
    summary = (
        f"📘 Groups scanned: {groups_scanned}/{len(groups)}\n"
        f"📝 Posts queued: {posts_queued} "
        f"(✅ {high_confidence} auto, 👀 {needs_approval} need approval)\n"
        f"⏭️ Skipped: {posts_skipped_dedup} dedup, {posts_skipped_score} low score"
    )
    print(f"""
=== Facebook Scan Complete ===
Groups scanned: {groups_scanned} / {len(groups)}
Posts evaluated: {posts_evaluated}
Posts queued for comments: {posts_queued}
  - High confidence (score >= {approval_threshold}): {high_confidence}
  - Needs approval ({relevance_threshold}-{approval_threshold}): {needs_approval}
Posts skipped — already engaged: {posts_skipped_dedup}
Posts skipped — below threshold: {posts_skipped_score}
""")
    print_status()
    skill_finished("fb-scanner", summary)


if __name__ == "__main__":
    run_scan()
