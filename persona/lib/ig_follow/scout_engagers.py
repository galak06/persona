"""Scrape likers + commenters on a competitor's recent posts.

Engagers (active interactors) are a higher-intent signal than passive
followers — a user who comments on a competitor post is shopping in
that category right now. Trade-off: smaller per-source pool and more
requests per candidate (one navigation per post, one extra for likes).

We deliberately skip likes-list scraping for posts past the first few:
recent posts have fresh, US-active engagers; older posts pull in
international and bot likes that dilute the signal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._dom import detect_action_block, detect_user_not_found
from .candidate import Candidate

if TYPE_CHECKING:
    from playwright.sync_api import Page


def _recent_post_urls(page: Page, source_handle: str, post_count: int) -> list[str]:
    """Return the first N post URLs from the source's profile grid."""
    page.goto(f"https://www.instagram.com/{source_handle}/", wait_until="domcontentloaded")
    detect_user_not_found(page)
    detect_action_block(page)

    # Post anchors are /p/{shortcode}/ or /reel/{shortcode}/.
    anchors = page.locator("a[href*='/p/'], a[href*='/reel/']")
    n = anchors.count()
    urls: list[str] = []
    seen: set[str] = set()
    for i in range(n):
        href = anchors.nth(i).get_attribute("href") or ""
        if not href or href in seen:
            continue
        seen.add(href)
        if href.startswith("/"):
            href = f"https://www.instagram.com{href}"
        urls.append(href)
        if len(urls) >= post_count:
            break
    return urls


def _scrape_likers(page: Page, source_handle: str, per_post_limit: int) -> list[Candidate]:
    """From the currently-open post page, open the likers dialog and scrape it.

    Returns empty list if the post hides the like count (some accounts
    do); IG renders no clickable likers link in that case.
    """
    likers_link = page.locator("a[href$='/liked_by/']").first
    try:
        likers_link.click(timeout=3000)
    except Exception:
        return []

    page.locator("div[role='dialog']").first.wait_for(state="visible", timeout=5000)
    detect_action_block(page)

    dialog = page.locator("div[role='dialog']").first
    anchors = dialog.locator("a[role='link']")
    n = anchors.count()
    out: list[Candidate] = []
    seen: set[str] = set()
    for i in range(n):
        href = anchors.nth(i).get_attribute("href") or ""
        if not href.startswith("/") or not href.endswith("/") or href.count("/") != 2:
            continue
        handle = href.strip("/").lower()
        if not handle or handle == source_handle or handle in seen:
            continue
        seen.add(handle)
        out.append(
            Candidate(
                handle=handle,
                source_handle=source_handle,
                source_signal="engager",
            )
        )
        if len(out) >= per_post_limit:
            break

    # Close the dialog so the next post navigation works cleanly.
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    return out


def _scrape_commenters(page: Page, source_handle: str, per_post_limit: int) -> list[Candidate]:
    """Scrape commenter handles from the currently-open post page.

    Comments live inline in the post layout (no modal). Each comment
    block contains an anchor to /{handle}/ — same shape as elsewhere.
    """
    # Filter to anchors that aren't the post owner's own link.
    anchors = page.locator("ul a[role='link'], article a[role='link']")
    n = anchors.count()
    out: list[Candidate] = []
    seen: set[str] = set()
    for i in range(n):
        href = anchors.nth(i).get_attribute("href") or ""
        if not href.startswith("/") or not href.endswith("/") or href.count("/") != 2:
            continue
        handle = href.strip("/").lower()
        if not handle or handle == source_handle or handle in seen:
            continue
        seen.add(handle)
        out.append(
            Candidate(
                handle=handle,
                source_handle=source_handle,
                source_signal="engager",
            )
        )
        if len(out) >= per_post_limit:
            break
    return out


def scout_engagers(
    page: Page,
    source_handle: str,
    post_count: int = 3,
    per_post_limit: int = 15,
) -> list[Candidate]:
    """Scrape engagers from the source's most recent posts.

    Args:
        page: Logged-in IG Playwright Page.
        source_handle: Competitor username (no @).
        post_count: How many recent posts to drill into. More = more
            candidates but also more navigation = higher block risk.
            Default 3 is the safe knee in the curve.
        per_post_limit: Per-post cap on both likers and commenters.
            Total candidates per source is bounded by
            `post_count * per_post_limit * 2`.

    Returns:
        Candidates with handle set; bio/display_name left None
        (scraping those would require N more navigations).

    Raises:
        IGActionBlockedError: From `_dom.detect_action_block`.
        IGUserNotFoundError: Source profile gone.
    """
    handle = source_handle.lower().lstrip("@")
    post_urls = _recent_post_urls(page, handle, post_count)

    out: list[Candidate] = []
    seen: set[str] = set()

    for url in post_urls:
        page.goto(url, wait_until="domcontentloaded")
        detect_action_block(page)

        for cand in _scrape_likers(page, handle, per_post_limit):
            if cand.handle not in seen:
                seen.add(cand.handle)
                out.append(cand)

        for cand in _scrape_commenters(page, handle, per_post_limit):
            if cand.handle not in seen:
                seen.add(cand.handle)
                out.append(cand)

    return out
