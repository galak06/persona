"""Did the comment we just submitted actually land on the post?

`lib/ig/comment_post.py` and `lib/fb/comment_post.py` both used to click their
submit button, ``time.sleep(3)`` and ``return True`` unconditionally, so a
submit that never landed reported success and a submit that landed then raised
reported failure — the adapters map any exception to
``CommentResult.failed(...)``. That ambiguity is what put two comments on one
post. This module is the shared answer: it polls the page that is already open
until our own text is visible in the thread.

It reads the open page rather than navigating. The post is on screen, and a
reload would cost a full page load per comment. That has one consequence worth
naming: the composer may still hold the text we just typed, so occurrences
still sitting inside a ``contenteditable``/``textarea`` are subtracted before
deciding — otherwise a submit that never went through would confirm itself.

The matcher keeps the same deliberate bias as
``lib/engagement/adapters/own_comment.py`` — generous, resolving ambiguity
toward "found" — and reuses that module's ``normalize_for_match`` so "did it
land?" and "is it already there?" can never disagree about what counts as the
same comment. A false positive costs at most one comment we never make; a
false negative costs a duplicate comment on a real post, in public.

Both platforms share this one implementation. They previously carried
byte-identical copies, which is exactly the shape that lets one platform's fix
silently miss the other.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from lib.engagement.adapters.own_comment import normalize_for_match

if TYPE_CHECKING:
    from playwright.sync_api import Page

# Bounded poll: both platforms render the new comment into the thread
# asynchronously.
CONFIRM_ATTEMPTS = 6
CONFIRM_INTERVAL_S = 1.0

# `page.evaluate(JS, normalized_prefix)` -> bool. Counts occurrences of our
# match key in the rendered page text and subtracts the ones still sitting in a
# composer, so text left behind by a submit that never went through is not
# mistaken for a posted comment. Everything else resolves toward "found".
COMMENT_LANDED_JS: str = """
(target) => {
    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
    const occurrences = (hay) => {
        let n = 0, i = 0;
        while (target && (i = hay.indexOf(target, i)) !== -1) { n++; i += target.length; }
        return n;
    };
    const total = occurrences(norm(document.body ? document.body.innerText : ''));
    if (total === 0) return false;
    let drafts = 0;
    for (const el of document.querySelectorAll('[contenteditable="true"], textarea')) {
        drafts += occurrences(norm(el.innerText || el.value || el.textContent || ''));
    }
    return total > drafts;
}
"""


def comment_landed(page: Page, comment: str, *, label: str) -> bool:
    """Poll the open post until our comment shows up in the thread.

    ``label`` is the platform tag used in the progress line (``"IG"`` / ``"FB"``)
    — the only thing that ever differed between the two copies of this function.

    An empty match key or a DOM read that blows up returns True: we cannot
    tell, and the generous direction is the cheap one — a false "found" costs
    at most one comment we never make, on one post.
    """
    target = normalize_for_match(comment)
    if not target:
        return True
    for attempt in range(CONFIRM_ATTEMPTS):
        if attempt:
            time.sleep(CONFIRM_INTERVAL_S)
        try:
            if page.evaluate(COMMENT_LANDED_JS, target) is True:
                return True
        except Exception as exc:
            print(f"    {label} confirm error: {type(exc).__name__} (assuming posted)", flush=True)
            return True
    return False
