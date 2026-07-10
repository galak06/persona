"""Submit a comment on a Facebook post via an authenticated Playwright page.

Extracted from ``scripts/comment_poster.py`` so the FB comment action
(``scripts/fb_comment.py``) and any other caller share one posting path
instead of duplicating the brittle FB DOM walk. The caller owns the browser
context/session; this function only drives one post → comment-box → submit.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page


def post_comment_fb(page: Page, post_url: str, comment: str) -> bool:
    """Navigate to ``post_url`` and submit ``comment``. Returns True on success.

    Best-effort DOM walk with multiple fallbacks (FB markup shifts across group
    types and profile-vs-page): click the placeholder to activate the editor,
    locate the ``contenteditable`` textbox, type, then click Send (or Enter).
    Returns False if the comment box can't be found.
    """
    page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(5)

    # Scroll down gradually to trigger the lazy-loaded comment section.
    for scroll_pct in [0.3, 0.5, 0.7, 0.9]:
        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {scroll_pct})")
        time.sleep(2)

    # Step 1: click the placeholder to activate the editor. The visible text is
    # the most reliable anchor ("Comment as <Name>" / "Write a public comment").
    try:
        placeholder = page.locator("text=/^Comment as /i").first
        if not placeholder.is_visible():
            placeholder = page.locator("text=/Write an? (public )?(comment|answer).*/i").first

        if placeholder.is_visible():
            placeholder.click()
            print("    Pre-click: clicked_placeholder", flush=True)
            time.sleep(2)
        else:
            print("    Pre-click: none", flush=True)
    except Exception as e:
        print(f"    Pre-click error: {e}", flush=True)

    # Step 2: find the contenteditable editor (now activated → a textbox).
    editor = page.locator('div[contenteditable="true"][role="textbox"]').first
    if not editor.is_visible():
        editor = page.locator('div[contenteditable="true"][aria-label*="comment" i]').first
    if not editor.is_visible():
        editor = page.locator('div[contenteditable="true"][aria-label*="Write" i]').first

    if not editor.is_visible():
        print("    Comment box: not_found", flush=True)
        return False

    print("    Comment box: found", flush=True)

    try:
        editor.click()
        time.sleep(1)
        page.keyboard.insert_text(comment)
        time.sleep(2)

        # Step 3: click the Send/Comment submit button (Enter as fallback).
        submit_btn = page.locator('div[aria-label="Comment"][role="button"]').first
        if not submit_btn.is_visible():
            submit_btn = page.locator('div[aria-label="Send"][role="button"]').first

        if submit_btn.is_visible():
            submit_btn.click()
            print("    Submit: clicked", flush=True)
            time.sleep(3)
            return True

        print("    Submit: not_found, pressing Enter", flush=True)
        page.keyboard.press("Enter")
        time.sleep(3)
        return True
    except Exception as e:
        print(f"    Error during typing/submit: {e}", flush=True)
        return False
