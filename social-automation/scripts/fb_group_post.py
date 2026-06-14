# pyright: reportMissingImports=false
# Pre-existing print()-based step logging throughout this script; structured
# log migration is deferred to a dedicated refactor (sys.path-based imports
# also force the pyright suppression — bootstrap rewires sys.path at runtime).
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
import os
import secrets
import sys
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.sync_api import Page

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "recipe-publisher"))

from lib.bootstrap import init_script

settings, log = init_script(__name__)

from comment_generator import validate_voice
from group_warmup import LINK_POST_WARMUP_HOURS, hours_until_warm, is_group_warm
from lib.fb.session import FbSession, build_fb_session
from lib.fb_composer_reel import (
    _attach_reel_in_composer,
    build_dry_run_plan,
    maybe_append_campaign_close,
    reel_target_categories,
)
from lib.fb_group_publish import assert_create_post_dialog, publish_to_group
from lib.groups.notes import append_group_note
from lib.local_env import get_brand_campaign
from lib.logger import log_step
from notifier import request_publish_approval, skill_finished, skill_started
from rate_limiter import can_act, record_action

if settings.paths is None:
    raise RuntimeError("settings.paths is unset; lib.config failed to resolve BRAND_DIR")
from lib import groups_db  # FB groups live in groups.db (was groups_tracker.json)

LOG_FILE = PROJECT_ROOT / "logs/engagement_log.jsonl"

# Daily cap on group posts is enforced via lib.rate_limiter.can_act —
# the actual cap value lives in lib/rate_limiter.py:DAILY_LIMITS["facebook:group_post"].
# Don't redefine the cap here; the rate_limiter is the single source of truth.

_RECIPE_KEYS = ("recipe", "homemade", "food", "treat", "pup", "nutrition")
_RUNNING_KEYS = ("running", "canicross", "trail", "gps", "tracker", "walk")


def classify(group_name: str) -> str:
    lo = group_name.lower()
    if any(k in lo for k in _RECIPE_KEYS):
        return "recipe"
    if any(k in lo for k in _RUNNING_KEYS):
        return "running"
    return "general"


def draft_for_recipe(title: str, url: str) -> str:
    body = (
        f"Made a batch of {title} with Nalla this weekend and they were gone "
        f"by Monday — she's been demanding them every morning since. "
        f"Full recipe with exact amounts, bake time, and swaps if you don't "
        f"have one of the ingredients on hand."
    )
    closer = "\n\nWhat's your dog's most-requested homemade treat?"
    return body + f"\n\nFull recipe: {url}" + closer


def draft_for_running(title: str, url: str) -> str:
    body = (
        f"Been testing homemade training treats for trail runs with Nalla — "
        f"{title} held up best so far. Calorie-dense, three ingredients, "
        f"doesn't crumble in a ziplock for a 60-minute run."
    )
    closer = "\n\nWhat do you pocket for your dog on long runs?"
    return body + f"\n\nFull recipe: {url}" + closer


def draft_for_general(title: str, url: str) -> str:
    body = (
        f"Sharing a quick one from our kitchen — {title}. "
        f"Easy, pantry-friendly, and Nalla actually works for them."
    )
    closer = "\n\nWhat's your go-to treat when you're out of the store-bought ones?"
    return body + f"\n\nFull breakdown: {url}" + closer


def draft_caption(group: dict[str, Any], title: str, url: str) -> str:
    """Caption with the URL inline in the body. The 'link in first comment' pattern
    is a FB Page-only tactic (algorithmically rewarded for our own posts) — for
    group posts the link belongs in the body, where members actually see it.
    """
    category = classify(group["group_name"])
    if category == "recipe":
        return draft_for_recipe(title, url)
    if category == "running":
        return draft_for_running(title, url)
    return draft_for_general(title, url)




def _is_big_group(group: dict[str, Any]) -> bool:
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


# Status enums for open_composer_and_post() return values. Replaces the legacy
# bool return so the caller can distinguish a live-published post from an
# admin-queue submission. See Fix #1 (May 25 2026): admins-only groups were
# being reported as "✅ Posted" because the submit JS treated 'Submit for
# approval' as identical to 'Post' — the resulting tracker writes locked the
# groups into the 24h dedup window even though no live post existed.
#
# Fix #2 (May 25 2026): added STATUS_SUBMIT_UNVERIFIED for the case where the
# publish button was clicked but the post never appeared in the group's
# /my_posted_content list within the verification timeout — covers FB
# silently dropping the post (auto-filter / shadowban / network race).
STATUS_POSTED = "posted"
STATUS_PENDING_APPROVAL = "pending_admin_approval"
STATUS_SUBMIT_UNVERIFIED = "submit_unverified"
STATUS_FAILED = "failed"

# Fix #2: how long to poll /my_posted_content for the just-published post.
# FB takes 3-15s typically; we sleep 6s before verifying and then poll up to
# this many seconds total. Set high enough to absorb slow group renders but
# low enough that a true-drop doesn't burn 2 min per group.
POST_VERIFY_TIMEOUT_S = 45
POST_VERIFY_POLL_INTERVAL_S = 5


def _post_fingerprint(caption: str, n: int = 60) -> str:
    """First N chars of the caption, normalized for fuzzy DOM matching.

    FB renders posts with extra whitespace + link-card injection that breaks
    exact substring matching. Lowercase + collapse whitespace before compare.
    """
    s = " ".join((caption or "").split()).lower()
    return s[:n]


def verify_post_visible(
    page: Page,
    group_url: str,
    caption: str,
    timeout_s: int = POST_VERIFY_TIMEOUT_S,
) -> tuple[bool, str | None]:
    """Navigate to {group_url}/my_posted_content and confirm the just-published
    post is actually visible. Returns (visible, permalink_or_None).

    Fix #2 (May 25 2026): the submit button click is necessary but not
    sufficient evidence of a live post. FB can silently drop a post (auto-
    moderation filter, shadowban, group spam filter) and the script has no
    way to know without checking the published-posts view. The "Forever
    Healthy Dogs" case from the 2026-05-24 smoke landed `✅ Posted` in the
    log but the post never appeared in the group — this verifier catches
    that class of failure.

    The /my_posted_content view lists every post you've made in the group,
    newest first. We poll for an article whose text content contains the
    first 60 chars of the caption (case-insensitive, whitespace-normalized).
    """
    target_url = group_url.rstrip("/") + "/my_posted_content"
    fingerprint = _post_fingerprint(caption)
    if not fingerprint:
        # Defensive: empty caption can't be fingerprinted, so we can't verify.
        # Return (False, None) — caller will mark STATUS_SUBMIT_UNVERIFIED.
        print("    verify: skipped (empty caption fingerprint)", flush=True)
        return False, None

    try:
        page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"    verify: navigation failed ({e})", flush=True)
        return False, None
    time.sleep(3)

    deadline = time.time() + timeout_s
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        result = page.evaluate(
            """(fp) => {
            // Find any [role="article"] whose normalized lowercase text
            // contains the fingerprint. Capture the nearest permalink-style
            // anchor (FB renders timestamped /groups/{id}/posts/{id}/ or
            // /permalink/{id}/ links inside the article header).
            const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
            const articles = Array.from(document.querySelectorAll('[role="article"]'));
            for (const a of articles) {
                const txt = norm(a.textContent || '');
                if (!txt.includes(fp)) continue;
                // Find a permalink anchor inside this article.
                const links = Array.from(a.querySelectorAll('a[href*="/groups/"], a[href*="/permalink/"], a[href*="/posts/"]'));
                let permalink = null;
                for (const ln of links) {
                    const h = ln.getAttribute('href') || '';
                    if (h.includes('/posts/') || h.includes('/permalink/')) {
                        permalink = h.startsWith('http') ? h : ('https://www.facebook.com' + h);
                        break;
                    }
                }
                return {found: true, permalink};
            }
            return {found: false, permalink: null};
        }""",
            fingerprint,
        )
        result = result or {"found": False, "permalink": None}
        if result.get("found"):
            permalink = result.get("permalink")
            print(
                f"    verify: visible (attempt {attempts}, permalink={permalink})",
                flush=True,
            )
            return True, permalink
        time.sleep(POST_VERIFY_POLL_INTERVAL_S)

    print(
        f"    verify: NOT visible after {timeout_s}s "
        f"({attempts} attempts) — post may have been filtered/dropped",
        flush=True,
    )
    return False, None


def open_composer_and_post(
    page: Page,
    group_url: str,
    text: str,
    link_url: str | None,
    reel_path: Path | None = None,
    reel_thumbnail: Path | None = None,
    no_submit: bool = False,
    screenshot_path: Path | None = None,
) -> tuple[str, str | None]:
    """Open the group, find the composer, type, optionally attach a reel, submit.

    Optionally comment the link. In reel mode the link card path is skipped —
    the URL is already inline in the body per group convention.

    Returns (status, permalink) where status is one of:
      - STATUS_POSTED: publish button clicked AND post verified visible in
        /my_posted_content. permalink is the scraped post URL when found
        (None if the verifier saw the article but no anchor).
      - STATUS_PENDING_APPROVAL: admin-queue submit ('Submit for approval'
        button clicked); no live post exists yet. permalink is None.
      - STATUS_SUBMIT_UNVERIFIED: publish button clicked but post NOT
        visible in /my_posted_content within POST_VERIFY_TIMEOUT_S — the
        post was likely auto-filtered, shadowbanned, or never landed.
        permalink is None.
      - STATUS_FAILED: composer never opened, editor never appeared, reel
        attach failed, or submit button not found. permalink is None.
    Call sites MUST branch on status — truthy check is no longer safe
    because STATUS_SUBMIT_UNVERIFIED also exits via a real flow (button
    was clicked) and must NOT be treated like STATUS_POSTED.

    When `no_submit=True`: runs the entire flow up to (not including) the final
    submit click, takes `screenshot_path` if provided, then closes the dialog.
    Returns (STATUS_POSTED, None) on success so existing smoke-test asserts
    keep working. Used by --no-submit smoke tests to verify the composer is
    the create-post modal (not a comment box) before any live run. See the
    May 2026 bug where the unscoped composer-box selector typed into the
    first visible comment box and silently published comments instead of
    group posts.
    """
    page.goto(group_url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(5)
    # Click the top-of-feed group composer placeholder. Two critical rules
    # (May 2026 bug fix — silently posted comments instead of group posts):
    #   1. MUST be a [role="button"] element — not the surrounding <div> that
    #      contains the placeholder text (and a bunch of sibling text like
    #      "Feeling/activity Check in Poll"). Clicking the outer div does NOT
    #      reliably open the create-post modal; clicking the inner role=button
    #      does.
    #   2. MUST NOT be inside a [role="article"] — that filters out
    #      "Write a comment" placeholders rendered inside feed posts.
    # textContent matching trims whitespace and lowercases, so handle both
    # "..." and "…" variants.
    clicked = page.evaluate(
        """() => {
        const btns = Array.from(document.querySelectorAll('[role="button"]'));
        const placeholder = btns.find(el => {
            if (el.closest('[role="article"]')) return false;  // skip comment boxes
            const t = (el.textContent || '').trim().toLowerCase();
            return t === 'write something...' || t === 'write something…' ||
                   t.startsWith('write something') ||
                   t === 'create a public post...' || t === 'create a public post…' ||
                   t.startsWith('create a public post') ||
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
    if clicked != "clicked":
        return STATUS_FAILED, None

    # Wait for the create-post modal dialog. The unscoped contenteditable query
    # below WILL also match the comment box of the first visible post on the
    # feed (FB renders them inline with the same data-lexical-editor attr).
    # Scoping into [role="dialog"] is the only reliable way to guarantee we
    # type into the create-post composer, not a comment box.
    from playwright.sync_api import TimeoutError as PWTimeout

    try:
        page.locator('[role="dialog"]').first.wait_for(state="visible", timeout=8000)
    except PWTimeout as e:
        print(f"    composer-dialog: not_found ({e})", flush=True)
        return STATUS_FAILED, None

    # The dialog opens before the Lexical editor mounts. Wait specifically for
    # a contenteditable inside any [role="dialog"] before evaluating the
    # selector list — otherwise we race the React mount and bail prematurely.
    try:
        page.locator(
            '[role="dialog"] [contenteditable="true"]'
        ).first.wait_for(state="visible", timeout=8000)
    except PWTimeout as e:
        print(f"    composer-editor: not_found ({e})", flush=True)
        # Debug: snapshot the page so we can see what dialog FB actually showed.
        if screenshot_path is not None:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(screenshot_path), full_page=False)
                print(f"    debug screenshot -> {screenshot_path}", flush=True)
            except Exception as se:
                print(f"    debug screenshot failed: {se}", flush=True)
        return STATUS_FAILED, None

    found = page.evaluate(
        """() => {
        // FB renders MULTIPLE sibling [role="dialog"] elements for the
        // create-post flow. One has aria-label="Create post" (title + close
        // chrome only); a sibling (no aria-label) actually contains the
        // editor with aria-placeholder="Create a public post…". Search ACROSS
        // ALL dialogs and pick the editable inside any of them. Never accept
        // an editable outside [role="dialog"] — those are comment boxes
        // (the May 2026 bug we're fixing). Also reject anything whose
        // aria-label/placeholder starts with "Comment" as a belt-and-braces
        // guard.
        const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'));
        if (dialogs.length === 0) return 'no_dialog';
        const sels = [
            '[contenteditable="true"][aria-placeholder*="create a public post" i]',
            '[contenteditable="true"][aria-placeholder*="what" i]',
            '[contenteditable="true"][aria-placeholder*="write" i]',
            '[contenteditable="true"][aria-label*="post" i]',
            '[contenteditable="true"][aria-label*="write" i]',
            '[contenteditable="true"][data-lexical-editor="true"]',
            '[contenteditable="true"][role="textbox"]',
        ];
        for (const dlg of dialogs) {
            for (const s of sels) {
                const box = dlg.querySelector(s);
                if (!box) continue;
                const al = (box.getAttribute('aria-label') || '').toLowerCase();
                const ap = (box.getAttribute('aria-placeholder') || '').toLowerCase();
                if (al.startsWith('comment') || ap.startsWith('comment')) continue;
                const dlgLabel = (dlg.getAttribute('aria-label') || '').toLowerCase();
                box.focus(); box.click();
                return 'found:' + s + '|dlg=' + (dlgLabel || '(none)') +
                       '|ph=' + (ap || al || '(none)');
            }
        }
        return 'not_found_in_dialog';
    }"""
    )
    print(f"    composer-box: {found}", flush=True)
    if not found.startswith("found"):
        # Debug: snapshot what FB actually rendered so we can adjust selectors.
        if screenshot_path is not None:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(screenshot_path), full_page=False)
                print(f"    debug screenshot -> {screenshot_path}", flush=True)
            except Exception as se:
                print(f"    debug screenshot failed: {se}", flush=True)
        return STATUS_FAILED, None

    # Belt-and-braces Python-side check: the JS above found *an* editable
    # inside *a* dialog, but doesn't verify the dialog is the create-post
    # composer (vs. e.g. a settings flyout that also renders a contenteditable).
    # assert_create_post_dialog raises RuntimeError with a clear message if
    # the open dialog doesn't carry create-post intent — we'd rather crash
    # the run than silently type into the wrong surface.
    assert_create_post_dialog(page)

    time.sleep(1)
    page.keyboard.type(text, delay=25)
    time.sleep(2)

    if reel_path is not None:
        attached = _attach_reel_in_composer(page, reel_path, reel_thumbnail)
        if not attached:
            print("    reel-attach failed; aborting submit", flush=True)
            return STATUS_FAILED, None

    if no_submit:
        if screenshot_path is not None:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_path), full_page=False)
            print(f"    no-submit: screenshot -> {screenshot_path}", flush=True)
        # Close the dialog (Escape works for FB's create-post modal).
        page.keyboard.press("Escape")
        time.sleep(1)
        # FB sometimes shows a "Discard post?" confirm — click Discard.
        page.evaluate(
            """() => {
            const dialog = document.querySelector('[role="dialog"]');
            if (!dialog) return;
            const btns = Array.from(dialog.querySelectorAll('[role="button"], button'));
            const discard = btns.find(b => {
                const t = (b.textContent || '').trim().toLowerCase();
                return t === 'discard' || t === 'discard post';
            });
            if (discard) discard.click();
        }"""
        )
        print("    no-submit: dialog closed without posting", flush=True)
        return STATUS_POSTED, None

    # Fix #1 (May 25 2026): split the publish vs approval-queue surfaces.
    # PUBLISH_LABELS are real "this post goes live now" buttons. APPROVAL_LABELS
    # are admins-only-group buttons that send the post to the group's admin
    # review queue (no live post until an admin acts). We need to know which
    # one FB actually showed us so the caller can:
    #   (a) skip the rate counter + 24h dedup write when it's only pending,
    #   (b) auto-reclassify the group's posting_mode to "admins_only".
    # The JS returns {clicked, kind} where kind ∈ {"publish","approval","none"}.
    submitted = page.evaluate(
        """() => {
        const dialog = document.querySelector('[role="dialog"]');
        const PUBLISH = new Set(['post', 'publish', 'share', 'submit', 'send']);
        const APPROVAL = new Set(['submit for approval', 'submit for review', 'request to post']);
        if (!dialog) return {clicked: false, kind: 'none', label: ''};
        const dbtns = Array.from(dialog.querySelectorAll('[role="button"], button'));
        // Two-pass: prefer a real publish button if BOTH are somehow rendered
        // (defensive — FB normally only renders one). Fall back to approval.
        const pickByKind = (set) => dbtns.find(b => {
            if (b.getAttribute('aria-disabled') === 'true') return false;
            const l = (b.getAttribute('aria-label') || '').trim().toLowerCase();
            const t = (b.textContent || '').trim().toLowerCase();
            return set.has(l) || set.has(t);
        });
        let btn = pickByKind(PUBLISH);
        let kind = 'publish';
        if (!btn) { btn = pickByKind(APPROVAL); kind = 'approval'; }
        if (!btn) return {clicked: false, kind: 'none', label: ''};
        const label = (btn.getAttribute('aria-label') || btn.textContent || '').trim();
        btn.click();
        return {clicked: true, kind, label};
    }"""
    )
    submitted = submitted or {"clicked": False, "kind": "none", "label": ""}
    clicked = bool(submitted.get("clicked"))
    kind = str(submitted.get("kind") or "none")
    label = str(submitted.get("label") or "")
    print(f"    submit: clicked={clicked} kind={kind} label={label!r}", flush=True)
    if not clicked:
        return STATUS_FAILED, None

    time.sleep(6)

    if kind == "approval":
        # Admin-queue submission — no live post exists. The caller MUST NOT
        # write last_reel_post_at / last_post_at (would dedup-lock the group
        # for 24h with no visible content). It MUST also NOT increment the
        # daily rate counter (no FB-trust cost yet — the post is invisible).
        # No permalink to return — the post isn't live yet.
        return STATUS_PENDING_APPROVAL, None

    # Fix #2 (May 25 2026): submit click is necessary but not sufficient.
    # Verify the post actually landed in the group's /my_posted_content view
    # before declaring success. Without this check, FB silently dropping the
    # post (auto-filter / shadowban / spam classifier) shows up in our logs
    # as ✅ Posted — see the 2026-05-24 "Forever Healthy Dogs" smoke where
    # the script reported success but the post never appeared in the group.
    visible, permalink = verify_post_visible(page, group_url, text)
    if not visible:
        # We have NO live post to attach a first-comment under, and we MUST
        # NOT mark this group as dedup-locked (the post may show up later
        # via slow propagation, or never — either way the caller should be
        # free to retry on the next campaign without burning a slot).
        return STATUS_SUBMIT_UNVERIFIED, None

    # If link_url provided, post it as the first comment under our (now
    # verified) post. Only run this for verified live publishes — there's
    # no post to comment under for approval-queue or unverified submissions.
    if link_url:
        _post_first_comment_link(page, link_url)

    return STATUS_POSTED, permalink


def _post_first_comment_link(page: Page, url: str) -> None:
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


def log_engagement(group: dict[str, Any], text: str) -> None:
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


EXIT_NO_POSTS = 22


def _health_check(session: FbSession) -> int:
    if session.is_authenticated():
        print(f"FB session OK (storage: {session.storage_path})")
        return 0
    print(f"SESSION_EXPIRED: {session.storage_path} missing or empty", file=sys.stderr)
    return 1


def main(session: FbSession) -> int:
    parser = argparse.ArgumentParser(description="Post a WP blog link to joined FB groups")
    parser.add_argument("--url", required=True, help="Blog post URL to share")
    parser.add_argument("--title", required=True, help="Blog post title (for the caption)")
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="verify FB session is authenticated and exit",
    )
    parser.add_argument("--only", help="group id (digits from URL) — limit to one group")
    parser.add_argument("--dry-run", action="store_true", help="draft + approve, skip posting")
    parser.add_argument(
        "--no-comment",
        action="store_true",
        help="skip the first-comment URL step (the auto-comment step is fragile — use when you'll add the URL manually)",
    )
    parser.add_argument(
        "--caption-override",
        help="Use this exact caption for every eligible group instead of the per-group template. Passed through voice validation.",
    )
    parser.add_argument(
        "--reel-path",
        type=Path,
        help="Path to a reel mp4 — attach as video instead of posting a link card. Body still carries the URL inline (group convention).",
    )
    parser.add_argument(
        "--reel-thumbnail",
        type=Path,
        help="Optional custom cover image (png/jpg) for the reel.",
    )
    parser.add_argument(
        "--no-submit",
        action="store_true",
        help="Run the full composer flow (open modal, type, attach reel) "
             "but do NOT click the final post button. Used for smoke tests "
             "verifying we're in the create-post modal, not a comment box.",
    )
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        # Default uses the system temp dir (per CLI convention for smoke-test
        # artifacts); operator can override via the flag. S108 ignored for
        # this specific CLI default — it's never a write target by accident.
        default=Path(tempfile.gettempdir()) / "slice5-fix",
        help="Where to save --no-submit composer screenshots (one per group).",
    )
    parser.add_argument(
        "--approval-timeout-seconds",
        type=int,
        default=None,
        help="Override the Telegram approval timeout (default 300s, "
             "see feedback_approval_before_browser.md). Timeout = auto-approve. "
             "Env var APPROVAL_TIMEOUT_SECONDS is read as a fallback so "
             "verification runs can shorten it without touching the CLI "
             "shape. Production default stays 300s.",
    )
    args = parser.parse_args()

    # Resolve approval timeout: CLI flag > env var > default 300s.
    # Per the approval-before-browser memory: timeout is in SECONDS and
    # treated as auto-approve (NOT skip) — keeps cron from blocking on an
    # idle Telegram while still publishing safely.
    approval_timeout = (
        args.approval_timeout_seconds
        if args.approval_timeout_seconds is not None
        else int(os.environ.get("APPROVAL_TIMEOUT_SECONDS", "300"))
    )

    reel_path: Path | None = args.reel_path
    reel_thumbnail: Path | None = args.reel_thumbnail
    reel_mode = reel_path is not None
    if reel_path is not None and not reel_path.exists():
        print(f"❌ reel path does not exist: {reel_path}", flush=True)
        sys.exit(2)

    campaign = get_brand_campaign()
    reel_categories = reel_target_categories(campaign) if reel_mode else set()

    skill_started("fb-group-post", f"sharing {args.title[:40]}")

    tracker = groups_db.load_all()
    joined = [g for g in tracker if g.get("status") == "joined"]

    # Skip groups that aren't post-able: only attempt posting_mode=direct
    # (admins_only/blocked would just waste a Telegram approval cycle).
    direct = [g for g in joined if g.get("posting_mode") == "direct"]

    # 72h warmup gate — newly joined groups need to age before we drop a link
    warm = []
    for g in direct:
        if is_group_warm(g["group_url"], LINK_POST_WARMUP_HOURS):
            warm.append(g)
        else:
            remaining = hours_until_warm(g["group_url"], LINK_POST_WARMUP_HOURS)
            print(
                f"  ⏭  {g['group_name'][:45]} — in {LINK_POST_WARMUP_HOURS}h warmup "
                f"({remaining:.1f}h remaining)",
                flush=True,
            )

    # Skip groups we already posted to in the last 24 hours — avoids duplicate
    # posts when the cron retries the same campaign within the day (1h was
    # too narrow; an interrupted run + restart could re-post the same group).
    # Reel mode reads its own marker (last_reel_post_at) so a link-card post
    # doesn't shadow a reel post (and vice versa) — they're independent surfaces.
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    last_key = "last_reel_post_at" if reel_mode else "last_post_at"
    fresh = []
    for g in warm:
        last = g.get(last_key)
        if last and not args.no_submit:
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

    if reel_mode and reel_categories:
        fresh = [g for g in fresh if classify(g["group_name"]) in reel_categories]
        print(
            f"reel-mode: filtered to categories={sorted(reel_categories)} "
            f"→ {len(fresh)} groups",
            flush=True,
        )

    print(f"Eligible groups: {len(fresh)}", flush=True)
    joined = fresh

    posted = skipped = 0

    if args.dry_run:
        lines = build_dry_run_plan(
            joined,
            draft_caption_fn=draft_caption,
            classify_fn=classify,
            title=args.title,
            url=args.url,
            caption_override=args.caption_override,
            campaign=campaign,
            reel_mode=reel_mode,
            reel_path=reel_path,
            reel_thumbnail=reel_thumbnail,
        )
        for line in lines:
            print(line, flush=True)
        summary = f"dry-run planned={len(lines) // 2}"
        skill_finished("fb-group-post", summary)
        print(f"\n=== Done (dry-run) === {summary}", flush=True)
        return 0

    # PER-GROUP LOOP — approval-first, then per-group browser open/close.
    # See feedback_approval_before_browser.md: Telegram MUST be sent with
    # browser CLOSED; open per group right before posting; close right after.
    rng = secrets.SystemRandom()
    for group in joined:
        # Daily cap check (skipped in --no-submit smoke runs).
        if not args.no_submit and not can_act("facebook", "group_post"):
            print("  ⏹  daily cap reached (rate_limiter)", flush=True)
            break

        caption = args.caption_override or draft_caption(group, args.title, args.url)
        if not caption:
            print(
                f"  ⏭  {group['group_name']} — no matching template (running-only?)",
                flush=True,
            )
            continue

        if reel_mode:
            caption = maybe_append_campaign_close(caption, campaign)

        # Brand publisher: FB group body intentionally carries the URL inline
        # (Page-only "link in first comment" tactic — see CLAUDE.md).
        valid, violations = validate_voice(
            caption, allow_own_url=True, allow_long=reel_mode
        )
        if not valid:
            print(f"  ⚠️  {group['group_name']}: voice fail {violations}", flush=True)
            skipped += 1
            continue

        link_for_comment = None  # URL is inline; never use first-comment for groups
        log_step(f"  → {group['group_name']} (~{group.get('member_count') or '?'})")

        # === APPROVAL GATE (browser is CLOSED here) =========================
        approval = request_publish_approval(
            platform="facebook",
            target=group["group_name"],
            post_preview=args.url,
            draft_caption=caption,
            timeout_seconds=approval_timeout,
        )
        if approval["action"] == "skipped":
            print("    skipped (explicit decline)", flush=True)
            skipped += 1
            continue
        # Treat anything else (approved/edited/timeout-as-approve) as go.
        final = approval.get("comment") or caption

        screenshot_path: Path | None = None
        if args.no_submit:
            gid = group["group_url"].rstrip("/").rsplit("/", 1)[-1]
            screenshot_path = args.screenshot_dir / f"composer-state-{gid}.png"

        # === BROWSER OPEN → ACT → CLOSE (one contiguous block) =============
        permalink: str | None = None
        try:
            status, permalink = publish_to_group(
                session=session,
                group_url=group["group_url"],
                composer_fn=open_composer_and_post,
                caption=final,
                link_for_comment=link_for_comment,
                reel_path=reel_path,
                reel_thumbnail=reel_thumbnail,
                no_submit=args.no_submit,
                screenshot_path=screenshot_path,
            )
        except Exception as e:
            print(f"    ERROR: {e}", flush=True)
            status = STATUS_FAILED
            permalink = None

        # --no-submit: smoke test, never updates rate counters or tracker.
        if args.no_submit:
            ok = status == STATUS_POSTED
            print(
                f"    {'✅ no-submit OK' if ok else '❌ no-submit failed'}"
                f" — screenshot: {screenshot_path}",
                flush=True,
            )
            if ok:
                posted += 1
            else:
                skipped += 1
            continue

        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        if status == STATUS_POSTED:
            log_engagement(group, final)
            group["last_post_at"] = now
            group["last_post_status"] = "posted"
            group["last_post_caption"] = final
            # Fix #2 (May 25 2026): capture per-group post permalink for audit.
            # Pre-Fix #2, the only proof a post landed was the script's own
            # log line — manual UI checking was the only verification path.
            # We now store the permalink the verifier scraped from the
            # /my_posted_content view, so future audits can click straight
            # through to the live post.
            if permalink:
                group["last_post_permalink"] = permalink
                if reel_mode:
                    group["last_reel_post_permalink"] = permalink
            if reel_mode:
                group["last_reel_post_at"] = now
                group["last_reel_caption"] = final
            groups_db.save_all(tracker)
            record_action("facebook", "group_post")
            posted += 1
            print(
                f"    ✅ Posted{f' — {permalink}' if permalink else ''}",
                flush=True,
            )
            # Spacer between groups; browser already closed so this is just
            # a Python sleep (not an idle Chromium session).
            time.sleep(rng.uniform(60, 180))
        elif status == STATUS_PENDING_APPROVAL:
            # Fix #1 (May 25 2026): admin-queue submission, NOT a live post.
            # - DO NOT increment the daily rate counter (no FB-trust cost).
            # - DO NOT write last_reel_post_at / last_post_at (would lock the
            #   group out of the 24h dedup window with nothing visible).
            # - DO auto-reclassify posting_mode to "admins_only" — the live
            #   evidence overrides whatever the tracker guessed at vetting time.
            #   Future runs will skip this group at the posting_mode=direct
            #   filter until/unless an approval-flow slice is built.
            # - DO record a status note so the user can audit + manually
            #   re-vet if the reclassification is wrong.
            group["last_post_status"] = "pending_admin_approval"
            group["last_post_caption"] = final
            group["posting_mode"] = "admins_only"
            note_text = (
                "Auto-reclassified to admins_only: composer "
                "showed 'Submit for approval' on reel post attempt."
            )
            existing_notes = group.get("notes") or []
            if isinstance(existing_notes, list):
                already = any(n.get("text") == note_text for n in existing_notes)
            else:
                already = note_text in str(existing_notes)
            if not already:
                append_group_note(group, note_text)
            groups_db.save_all(tracker)
            skipped += 1
            print(
                "    ⏳ Pending admin approval — no live post yet; "
                "group auto-reclassified to admins_only.",
                flush=True,
            )
            time.sleep(rng.uniform(60, 180))
        elif status == STATUS_SUBMIT_UNVERIFIED:
            # Fix #2 (May 25 2026): publish button was clicked but the post
            # never showed up in /my_posted_content within the verification
            # window. Treat exactly like Fix #1's pending case for tracker
            # writes (no rate counter, no dedup lock) so the user can retry
            # the group on the next campaign without it being silently
            # locked out by a stale dedup marker. DO record a flag so future
            # audits can spot groups that keep silently dropping posts —
            # repeated unverified submits suggest a spam-filter / shadowban
            # situation that warrants manual investigation rather than retry.
            group["last_post_status"] = "submit_unverified"
            group["last_post_caption"] = final
            unverified_note = (
                "Submit clicked but post not visible in "
                f"/my_posted_content within {POST_VERIFY_TIMEOUT_S}s — "
                "likely auto-filter / spam classifier / shadowban. "
                "Investigate before re-posting."
            )
            existing_notes = group.get("notes") or []
            if isinstance(existing_notes, list):
                already = any(
                    n.get("text") == unverified_note for n in existing_notes
                )
            else:
                already = unverified_note in str(existing_notes)
            if not already:
                append_group_note(group, unverified_note)
            groups_db.save_all(tracker)
            skipped += 1
            print(
                "    ⚠ Submit unverified — post not visible in "
                "/my_posted_content; no rate counter / dedup write.",
                flush=True,
            )
            time.sleep(rng.uniform(60, 180))
        else:
            skipped += 1
            print("    ❌ post failed", flush=True)

    summary = f"posted={posted} skipped={skipped}"
    skill_finished("fb-group-post", summary)
    print(f"\n=== Done === {summary}", flush=True)
    # See distribute_fb_groups.py: reel runs that landed zero posts must NOT
    # mark the campaign as distributed. Link-card mode keeps the legacy
    # rc=0 contract; only reel-mode opts into the stricter exit-code gate.
    if reel_mode and posted == 0:
        return EXIT_NO_POSTS
    return 0


if __name__ == "__main__":
    # Composition root: build the brand-scoped FB session ONCE and inject
    # it into main(). PlaywrightFbSession.__init__ is cheap (browser only
    # launches on .page()), so --dry-run pays nothing for this.
    #
    # --health-check bypasses argparse's required=True on --url/--title
    # via a sentinel scan: the flag is meant to short-circuit early in
    # smoke/cron probes without forcing the operator to pass dummy values.
    if "--health-check" in sys.argv:
        sys.exit(_health_check(build_fb_session()))
    sys.exit(main(build_fb_session()))
