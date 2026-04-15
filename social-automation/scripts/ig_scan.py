"""
Instagram Hashtag Scanner — CLI version using Playwright.
Uses saved session state (from ig_login.py) to browse Instagram.
Scans hashtags, likes qualifying posts, queues top candidates for comments.

Usage:
    1. First time: python scripts/ig_login.py   (log in, save session)
    2. Then:       python scripts/ig_scan.py     (scan hashtags)
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

# Ensure lib is importable
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from comment_generator import score_relevance
from deduplication import is_duplicate, mark_engaged
from notifier import skill_started, skill_finished, skill_error, skill_skipped
from rate_limiter import can_act, print_status, record_action, wait_random_delay

SESSION_FILE = PROJECT_ROOT / ".claude/state/instagram_session.json"
QUEUE_FILE = PROJECT_ROOT / ".claude/state/comment_queue.json"
LAST_RUN_FILE = PROJECT_ROOT / ".claude/state/last_run.json"
ERROR_LOG = PROJECT_ROOT / "logs/errors.log"
CONFIG_FILE = PROJECT_ROOT / "config.json"
HASHTAG_FILE = PROJECT_ROOT / "data/instagram_accounts.csv"


def load_config() -> dict:
    with CONFIG_FILE.open() as f:
        return json.load(f)


def log_error(msg: str) -> None:
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with ERROR_LOG.open("a") as f:
        f.write(f"[{ts}] {msg}\n")


def load_hashtags() -> list[dict]:
    """Load today's hashtags from CSV based on scan frequency."""
    today = date.today()

    rows = []
    with HASHTAG_FILE.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            freq = row.get("scan_frequency", "").strip()
            if should_scan_today(freq, today):
                rows.append(row)
    return rows


def should_scan_today(freq: str, today: date) -> bool:
    if freq == "daily":
        return True
    if freq == "every_2_days":
        return today.toordinal() % 2 == 0
    if freq == "weekly":
        return today.weekday() == 0  # Mondays
    return False


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


def parse_like_count(text: str) -> int:
    """Parse Instagram like count from text like '1,234 likes' or '12.5K likes'."""
    if not text:
        return 0
    text = text.lower().replace(",", "")
    m = re.search(r"([\d.]+)\s*k", text)
    if m:
        return int(float(m.group(1)) * 1000)
    m = re.search(r"([\d.]+)\s*m", text)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"(\d+)", text)
    if m:
        return int(m.group(1))
    return 0


def parse_comment_count(text: str) -> int:
    """Parse comment count from text like 'View all 42 comments'."""
    if not text:
        return 0
    m = re.search(r"(\d+)\s*comment", text.lower())
    if m:
        return int(m.group(1))
    return 0


def ig_score_adjustments(base_score: float, like_count: int) -> float:
    """Apply IG-specific scoring adjustments on top of base relevance score."""
    score = base_score
    if like_count < 500:
        score += 0.15  # not viral, real engagement possible
    if like_count > 5000:
        score -= 0.20  # we'd be lost in the noise
    return round(score, 2)


# --- JavaScript for extracting posts from a hashtag page ---

EXTRACT_HASHTAG_POSTS_JS = """
() => {
    const links = Array.from(document.querySelectorAll('a[href*="/p/"]'));
    const posts = [];
    const seen = new Set();

    for (const a of links) {
        const href = a.getAttribute('href') || '';
        const match = href.match(/\\/p\\/([^\\/]+)/);
        if (!match) continue;
        const postId = match[1];
        if (seen.has(postId)) continue;
        seen.add(postId);

        posts.push({
            url: 'https://www.instagram.com' + href,
            post_id: postId,
        });
    }
    return posts.slice(0, 15);
}
"""

EXTRACT_POST_DETAILS_JS = """
() => {
    const result = {caption: '', like_text: '', comment_text: '', author: ''};

    // Caption — multiple selector strategies
    const h1 = document.querySelector('h1');
    if (h1) result.caption = h1.innerText || '';

    if (!result.caption) {
        // Fallback: look for the main text block in the post
        const spans = document.querySelectorAll('span[dir="auto"]');
        for (const span of spans) {
            const t = span.innerText || '';
            if (t.length > 30) {
                result.caption = t;
                break;
            }
        }
    }

    // Author
    const authorLink = document.querySelector(
        'header a[href]:not([href="/"])'
    );
    if (authorLink) {
        const href = authorLink.getAttribute('href') || '';
        result.author = href.replace(/\\x2f/g, '');
    }
    // Fallback: try the first link with a username-like path
    if (!result.author) {
        const links = document.querySelectorAll('a[href^="/"]');
        for (const a of links) {
            const h = a.getAttribute('href') || '';
            if (h.match(/^\\/[a-zA-Z0-9_.]+\\/$/) && h !== '/') {
                result.author = h.replace(/\\x2f/g, '');
                break;
            }
        }
    }

    // Like count
    const likeSection = document.querySelector(
        'section span:has(> span), ' +
        'a[href*="liked_by"] span, ' +
        'button span'
    );
    const allSpans = document.querySelectorAll('span');
    for (const s of allSpans) {
        const t = s.innerText || '';
        if (t.match(/\\d.*like/i) || t.match(/like.*\\d/i)) {
            result.like_text = t;
            break;
        }
    }

    // Comment count
    for (const s of allSpans) {
        const t = s.innerText || '';
        if (t.match(/view.*\\d+.*comment/i) || t.match(/\\d+.*comment/i)) {
            result.comment_text = t;
            break;
        }
    }

    return result;
}
"""

CLICK_LIKE_JS = """
() => {
    // Find the like button (heart icon) — multiple strategies
    const svgs = document.querySelectorAll('svg[aria-label="Like"]');
    for (const svg of svgs) {
        const btn = svg.closest('[role="button"]') ||
                    svg.closest('button') ||
                    svg.parentElement;
        if (btn) {
            btn.click();
            return 'liked';
        }
    }

    // Fallback: aria-label on the button itself
    const btns = document.querySelectorAll(
        '[aria-label="Like"][role="button"], button[aria-label="Like"]'
    );
    if (btns.length > 0) {
        btns[0].click();
        return 'liked';
    }

    // Check if already liked
    const unlikeSvgs = document.querySelectorAll('svg[aria-label="Unlike"]');
    if (unlikeSvgs.length > 0) {
        return 'already_liked';
    }

    return 'not_found';
}
"""

# Known competitor brand accounts — never like their posts
COMPETITOR_ACCOUNTS = {
    "tractive", "tractivepets", "ficollar", "fidogs",
    "whistlepet", "whistle", "linkakc",
}

# Our own account — skip to avoid self-engagement
OWN_ACCOUNT = "dogfoodandfun"


def dismiss_ig_overlays(page) -> None:
    """Dismiss Instagram popups (notifications, cookies, login prompts)."""
    selectors = [
        "button:has-text('Not Now')",
        "button:has-text('Cancel')",
        "button:has-text('Decline')",
        "button:has-text('Accept')",  # cookie consent
        "[aria-label='Close']",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel)
            if btn.count() > 0:
                btn.first.click(timeout=2000)
                time.sleep(1)
                return
        except Exception:
            pass


def run_scan() -> None:
    """Main scan entry point."""
    from playwright.sync_api import sync_playwright

    print("=== Instagram Hashtag Scanner (CLI) ===\n")

    # Re-run guard — skip if already ran successfully today
    last_run = load_last_run()
    ig_last = last_run.get("ig_scanner", {})
    ig_last_date = (ig_last.get("last_run_at") or "")[:10]
    if ig_last_date == date.today().isoformat() and ig_last.get("status") == "success":
        msg = f"Already ran today — liked {ig_last.get('posts_liked', 0)} posts, queued {ig_last.get('posts_queued_for_comment', 0)} for comments"
        print(f"SKIP: ig_scanner already ran successfully today ({ig_last_date}).")
        print("Use --force to override.")
        skill_skipped("ig-scanner", msg)
        if "--force" not in sys.argv:
            return
        print("--force detected, re-running.\n")

    # Check session file
    if not SESSION_FILE.exists():
        print("ERROR: No saved Instagram session found.")
        print("Run this first:  python scripts/ig_login.py")
        return

    # Pre-flight: rate limits
    if not can_act("instagram", "like"):
        print("ABORT: Daily IG like limit reached. Try again tomorrow.")
        skill_skipped("ig-scanner", "Daily IG like limit reached")
        print_status()
        return

    skill_started("ig-scanner", "Scanning Instagram hashtags for posts to like/comment")
    print_status()

    # Load hashtags for today
    hashtags = load_hashtags()
    print(f"Hashtags to scan today: {len(hashtags)}")
    for h in hashtags:
        print(f"  - {h['hashtag']} (tier {h.get('tier', '?')}, {h.get('category', '?')})")
    print()

    if not hashtags:
        print("No hashtags scheduled for today. Done.")
        return

    # Load config and queue
    config = load_config()
    queue = load_queue()

    relevance_threshold = config["content_analysis"]["relevance_threshold"]
    ig_comment_threshold = 0.85  # higher bar for IG comments

    # Stats
    hashtags_scanned = 0
    posts_evaluated = 0
    posts_liked = 0
    posts_queued = 0
    posts_skipped_dedup = 0
    posts_skipped_score = 0
    posts_skipped_competitor = 0

    # Candidates for comment queue (collect all, pick top 2 at end)
    comment_candidates: list[dict] = []

    with sync_playwright() as p:
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

        # Quick login check
        print("Checking Instagram session...")
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        time.sleep(4)

        if "login" in page.url.lower() or "accounts/login" in page.url.lower():
            print("ABORT: Instagram session expired.")
            print("Re-run:  python scripts/ig_login.py")
            log_error("SESSION_EXPIRED: Instagram login required")
            browser.close()
            return

        print("Instagram session OK.\n")

        # Dismiss any startup overlays
        dismiss_ig_overlays(page)
        time.sleep(2)

        for htag_row in hashtags:
            hashtag = htag_row["hashtag"].strip().lstrip("#")
            category = htag_row.get("category", "general").strip()

            if not can_act("instagram", "like"):
                print(f"\nLike limit reached — stopping after {hashtags_scanned} hashtags.")
                break

            print(f"\n--- Scanning: #{hashtag} ---")
            tag_url = f"https://www.instagram.com/explore/tags/{hashtag}/"
            print(f"    URL: {tag_url}")

            try:
                page.goto(tag_url, wait_until="domcontentloaded")
                time.sleep(4)

                # Check for blocked/unavailable hashtag
                body_text = page.inner_text("body")[:500].lower()
                if "sorry" in body_text and "page isn't available" in body_text:
                    print(f"    SKIP: Hashtag #{hashtag} is blocked or unavailable.")
                    log_error(f"HASHTAG_BLOCKED: #{hashtag}")
                    continue

                dismiss_ig_overlays(page)

                # Scroll to load more posts
                for _ in range(2):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(2)

                hashtags_scanned += 1

                # Extract post links
                post_links = page.evaluate(EXTRACT_HASHTAG_POSTS_JS)
                print(f"    Posts found: {len(post_links)}")

                if not post_links:
                    print("    No posts extracted. Skipping hashtag.")
                    continue

                for post_info in post_links:
                    post_url = post_info["url"]
                    post_id = post_info["post_id"]

                    if not can_act("instagram", "like"):
                        print("    Like limit reached mid-scan.")
                        break

                    posts_evaluated += 1

                    # Dedup check
                    if is_duplicate("instagram", post_id):
                        posts_skipped_dedup += 1
                        continue

                    # Navigate to individual post
                    try:
                        page.goto(post_url, wait_until="domcontentloaded")
                        time.sleep(3)
                    except Exception as e:
                        log_error(f"POST_NAVIGATION_FAILED: {post_id} — {e}")
                        continue

                    dismiss_ig_overlays(page)

                    # Extract post details
                    try:
                        details = page.evaluate(EXTRACT_POST_DETAILS_JS)
                    except Exception:
                        details = {
                            "caption": "", "like_text": "",
                            "comment_text": "", "author": "",
                        }

                    caption = details.get("caption", "")[:800]
                    author = details.get("author", "").strip()

                    # Fallback: parse author from caption start
                    # IG captions often render as "username  3w Caption..."
                    if not author and caption:
                        m = re.match(r"^([a-zA-Z0-9_.]+)\s", caption)
                        if m:
                            author = m.group(1)
                    like_count = parse_like_count(details.get("like_text", ""))
                    comment_count = parse_comment_count(
                        details.get("comment_text", "")
                    )

                    snippet = caption[:60].replace("\n", " ")
                    print(f"    [{posts_evaluated}] @{author}: {snippet}...")
                    print(
                        f"        likes~{like_count} "
                        f"comments~{comment_count}"
                    )

                    # Skip own account
                    if author.lower() == OWN_ACCOUNT:
                        print("        SKIP: own account")
                        continue

                    # Skip competitor accounts
                    if author.lower() in COMPETITOR_ACCOUNTS:
                        posts_skipped_competitor += 1
                        print("        SKIP: competitor account")
                        continue

                    # Score relevance
                    meta = {
                        "comment_count": comment_count,
                        "hours_old": 12,  # conservative estimate
                    }
                    base_score = score_relevance(caption, meta)
                    score = ig_score_adjustments(base_score, like_count)
                    print(
                        f"        score={score} "
                        f"(threshold={relevance_threshold})"
                    )

                    if score < relevance_threshold:
                        posts_skipped_score += 1
                        continue

                    # Like the post
                    try:
                        like_result = page.evaluate(CLICK_LIKE_JS)
                    except Exception:
                        like_result = "error"

                    if like_result == "liked":
                        record_action("instagram", "like")
                        mark_engaged("instagram", post_id, "like", hashtag)
                        posts_liked += 1
                        print(f"        LIKED (#{posts_liked})")
                    elif like_result == "already_liked":
                        print("        already liked")
                    else:
                        print(f"        like button: {like_result}")
                        log_error(
                            f"LIKE_BUTTON_NOT_FOUND: {post_id} "
                            f"result={like_result}"
                        )

                    # Collect comment candidates (higher bar)
                    if score >= ig_comment_threshold and "?" in caption:
                        comment_candidates.append({
                            "platform": "instagram",
                            "post_url": post_url,
                            "post_id": post_id,
                            "post_text": caption[:600],
                            "hashtag": hashtag,
                            "author": author,
                            "category": category,
                            "relevance_score": score,
                            "like_count": like_count,
                            "queued_at": datetime.now(timezone.utc).isoformat(),
                            "status": "pending",
                            "requires_approval": True,
                        })

                    # Delay between posts
                    wait_random_delay("instagram", "like")

            except Exception as e:
                msg = f"Error scanning #{hashtag}: {e}"
                print(f"    ERROR: {e}")
                log_error(msg)
                continue

            # Delay between hashtag pages
            time.sleep(5)

        # Save refreshed session cookies
        context.storage_state(path=str(SESSION_FILE))
        browser.close()

    # Queue top comment candidates (max 2 per day)
    comment_budget = 2
    existing_ig_today = sum(
        1 for q in queue
        if q.get("platform") == "instagram"
        and q.get("queued_at", "").startswith(date.today().isoformat())
    )
    comment_budget -= existing_ig_today

    # Sort by score descending, take top N
    comment_candidates.sort(key=lambda c: c["relevance_score"], reverse=True)
    for candidate in comment_candidates[:max(0, comment_budget)]:
        queue.append(candidate)
        posts_queued += 1
        print(
            f"\nQUEUED for comment: @{candidate['author']} "
            f"score={candidate['relevance_score']} "
            f"#{candidate['hashtag']}"
        )

    save_queue(queue)

    # Update last run — mark success so re-run guard skips this on next call
    last_run["ig_scanner"] = {
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "hashtags_scanned": hashtags_scanned,
        "posts_liked": posts_liked,
        "posts_queued_for_comment": posts_queued,
        "status": "success",
    }
    save_last_run(last_run)

    # Summary
    like_status = (
        "8" if not can_act("instagram", "like") else "?"
    )
    print(f"""
=== Instagram Scan Complete ===
Hashtags scanned today: {hashtags_scanned}
Posts evaluated: {posts_evaluated}
Posts liked: {posts_liked} / 8 daily limit
Posts queued for comments: {posts_queued} / 2 daily limit
Posts skipped — already engaged: {posts_skipped_dedup}
Posts skipped — below threshold: {posts_skipped_score}
Posts skipped — competitor account: {posts_skipped_competitor}
""")

    if comment_candidates:
        print("Top comment candidates:")
        for i, c in enumerate(comment_candidates[:5], 1):
            snip = c["post_text"][:50].replace("\n", " ")
            print(
                f"  {i}. @{c['author']} — \"{snip}...\" "
                f"(score: {c['relevance_score']}) — #{c['hashtag']}"
            )
        print()

    print_status()
    summary = (
        f"📸 Hashtags scanned: {hashtags_scanned}\n"
        f"❤️ Posts liked: {posts_liked}/8\n"
        f"💬 Queued for comment: {posts_queued}/2\n"
        f"⏭️ Skipped: {posts_skipped_dedup} dedup, {posts_skipped_score} low score, {posts_skipped_competitor} competitor"
    )
    skill_finished("ig-scanner", summary)


if __name__ == "__main__":
    run_scan()
