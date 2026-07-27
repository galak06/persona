"""Scrape a competitor's followers tab via Playwright.

Why followers-tab over hashtag-tag scraping: a follower is already
opted-in to similar content, which is a much higher-precision signal
than a hashtag passerby. Cost: lower-intent than engagers.

DOM stability disclaimer
------------------------
Instagram ships UI changes weekly. The selectors below favor stable
attributes (role, href patterns, aria-label) over CSS classnames
(which are hashed and rotate). Even so, expect to retune when IG
ships a redesign. The action-block detection in `_detect_action_block`
is the most important thing to keep working — it's what protects the
account from escalating blocks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._dom import detect_action_block, detect_user_not_found
from .candidate import Candidate

if TYPE_CHECKING:
    from playwright.sync_api import Page


def _open_followers_modal(page: Page, source_handle: str) -> None:
    """Navigate to the source's profile and open the followers dialog."""
    page.goto(f"https://www.instagram.com/{source_handle}/", wait_until="domcontentloaded")
    detect_user_not_found(page)
    detect_action_block(page)

    # Followers link is anchored to /{handle}/followers/ — stable URL pattern.
    followers_link = page.locator(f"a[href$='/{source_handle}/followers/']").first
    followers_link.click(timeout=5000)

    # Modal renders as role="dialog". Wait for it before scrolling.
    page.locator("div[role='dialog']").first.wait_for(state="visible", timeout=8000)
    detect_action_block(page)


def _extract_visible_rows(page: Page, source_handle: str) -> list[Candidate]:
    """Pull username + display_name from every visible row in the dialog.

    Each row is an anchor whose href is `/<username>/`. Skip the source's
    own handle (appears as a self-link in some layouts).
    """
    dialog = page.locator("div[role='dialog']").first
    anchors = dialog.locator("a[role='link']")
    n = anchors.count()
    out: list[Candidate] = []
    seen: set[str] = set()
    for i in range(n):
        a = anchors.nth(i)
        try:
            href = a.get_attribute("href") or ""
        except Exception:
            continue
        # IG profile anchors are exactly "/<handle>/" — filter the rest.
        if not href.startswith("/") or not href.endswith("/") or href.count("/") != 2:
            continue
        handle = href.strip("/").lower()
        if not handle or handle == source_handle or handle in seen:
            continue
        seen.add(handle)
        try:
            display = a.inner_text(timeout=500).strip()
        except Exception:
            display = ""
        out.append(
            Candidate(
                handle=handle,
                source_handle=source_handle,
                source_signal="follower",
                display_name=display or None,
            )
        )
    return out


def scout_followers(
    page: Page,
    source_handle: str,
    limit: int = 50,
    max_scrolls: int = 20,
) -> list[Candidate]:
    """Scrape up to `limit` followers from `source_handle`.

    Args:
        page: A ready Playwright Page on a logged-in IG session.
        source_handle: Competitor username (no @).
        limit: Maximum candidates to return.
        max_scrolls: Safety cap on scroll iterations. IG sometimes
            silently stops loading new rows even while the dialog is
            scrollable — this bounds the wait.

    Returns:
        Candidates with handle + display_name populated. Bio and
        follower_count are not scraped at this layer (would require
        clicking into each profile, doubling the request volume).

    Raises:
        IGActionBlockedError: IG surfaced a block dialog. Abort the batch.
        IGUserNotFoundError: Source profile no longer exists.
    """
    handle = source_handle.lower().lstrip("@")
    _open_followers_modal(page, handle)

    dialog = page.locator("div[role='dialog']").first
    out: list[Candidate] = []
    seen: set[str] = set()

    for _ in range(max_scrolls):
        rows = _extract_visible_rows(page, handle)
        for cand in rows:
            if cand.handle not in seen:
                seen.add(cand.handle)
                out.append(cand)
                if len(out) >= limit:
                    return out

        # Scroll the dialog by injecting a scrollTop bump — page-level
        # scroll doesn't move the modal's internal scroll container.
        try:
            dialog.evaluate(
                "el => { const s = el.querySelector('[role=\"dialog\"] > div > div > div')"
                " || el; s.scrollTop = s.scrollHeight; }"
            )
        except Exception:
            break
        page.wait_for_timeout(1200)
        detect_action_block(page)

    return out
