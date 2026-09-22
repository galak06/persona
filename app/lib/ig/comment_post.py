"""Submit a comment on an Instagram post via an authenticated Playwright page.

Extracted from ``scripts/comment_poster.py`` so the IG comment action
(``scripts/ig_comment.py``) owns one posting path instead of duplicating the
DOM walk. The caller owns the browser context/session; this function drives one
post → comment-box → submit → **confirm**.

The confirm step is new and load-bearing. This function used to click Post,
``time.sleep(3)`` and ``return True`` unconditionally, so a submit that never
landed reported success and a submit that landed then raised reported failure —
``lib/engagement/adapters/instagram.py:237-238`` maps any exception to
``CommentResult.failed(...)``. That ambiguity is what put two comments on one
post. Now the return value means "our text is visible in the thread": True lets
``lib/engagement/comment_submit.py`` settle the claim immediately, False leaves
the claim ``pending`` for ``scripts/comment_verify.py`` to adjudicate.

The confirmation itself lives in ``lib/engagement/comment_confirm.py``, shared
with the Facebook half: the two used to carry byte-identical copies of it, and
that is exactly the shape that lets one platform's fix silently miss the other.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from lib.engagement.comment_confirm import comment_landed

if TYPE_CHECKING:
    from playwright.sync_api import Page


def post_comment_ig(
    page: Page,
    post_url: str,
    comment: str,
    *,
    skip_navigation: bool = False,
) -> bool:
    """Navigate to ``post_url`` and submit ``comment``. True == CONFIRMED live.

    Tries textarea → contenteditable → any form textarea to locate the comment
    box, types the comment, clicks Post (Enter as fallback), then polls the
    open page until the comment appears in the thread. Returns False if the
    comment box can't be found, or if the submit could not be confirmed — the
    caller treats that as "unconfirmed", not "did not post" (see the module
    docstring).

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

    time.sleep(1)
    landed = comment_landed(page, comment, label="IG")
    print(f"    IG confirm: {'found' if landed else 'not_found'}", flush=True)
    return landed
