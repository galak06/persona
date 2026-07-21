"""Submit a comment on an Instagram post via an authenticated Playwright page.

Extracted from ``scripts/comment_poster.py`` so the IG comment action
(``scripts/ig_comment.py``) owns one posting path instead of duplicating the
DOM walk. The caller owns the browser context/session; this function drives one
post → comment-box → submit.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page


def post_comment_ig(
    page: Page,
    post_url: str,
    comment: str,
    *,
    skip_navigation: bool = False,
) -> bool:
    """Navigate to ``post_url`` and submit ``comment``. Returns True on success.

    Tries textarea → contenteditable → any form textarea to locate the comment
    box, types the comment, then clicks Post (Enter as fallback). Returns False
    if the comment box can't be found.

    ``skip_navigation`` suppresses the goto + settle wait for callers that have
    already landed the page on ``post_url`` — the single-pass scanner likes and
    comments in one visit, so re-navigating costs a full page load per comment
    for nothing. Defaults to False so ``scripts/ig_comment.py``, which arrives
    from a queue with the page elsewhere, is unaffected.
    """
    if not skip_navigation:
        page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

    found = page.evaluate(
        """() => {
        // IG uses a textarea for comments; fall back to contenteditable / any form.
        const textarea = document.querySelector('textarea[aria-label*="comment" i]') ||
                         document.querySelector('textarea[placeholder*="comment" i]') ||
                         document.querySelector('textarea[placeholder*="Add a comment" i]');
        if (textarea) { textarea.click(); textarea.focus(); return 'found:textarea'; }

        const ce = document.querySelector('[contenteditable="true"][role="textbox"]');
        if (ce) { ce.click(); ce.focus(); return 'found:contenteditable'; }

        const forms = document.querySelectorAll('form');
        for (const f of forms) {
            const ta = f.querySelector('textarea');
            if (ta) { ta.click(); ta.focus(); return 'found:form_textarea'; }
        }
        return 'not_found';
    }"""
    )
    print(f"    IG comment box: {found}", flush=True)

    if not found.startswith("found"):
        return False

    time.sleep(1)
    page.keyboard.type(comment, delay=30)
    time.sleep(2)

    sub = page.evaluate(
        """() => {
        const btns = Array.from(document.querySelectorAll('button, [role="button"], div[tabindex="0"]'));
        const post = btns.find(b => {
            const text = (b.textContent || '').trim().toLowerCase();
            return text === 'post' || text === 'submit';
        });
        if (post) { post.click(); return 'clicked'; }
        return 'not_found';
    }"""
    )
    if sub != "clicked":
        page.keyboard.press("Enter")
    print(f"    IG submit: {sub}", flush=True)

    time.sleep(3)
    return True
