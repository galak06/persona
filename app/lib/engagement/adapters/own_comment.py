"""Read primitive: does our own comment already exist on a post?

The submit path cannot answer this. `lib/ig/comment_post.py` types a comment,
clicks Post, and returns True unconditionally; any exception raised *after*
that click is caught upstream and mapped to `CommentResult.failed(...)`. A
failed submit and a succeeded-then-crashed submit are indistinguishable at the
call site, which is how the same post got two identical comments. The only
honest answer comes from going back and LOOKING at the post — that is what
this module does.

Why the matcher is deliberately generous
----------------------------------------
It matches a whitespace-collapsed, case-insensitive 80-character prefix of our
drafted text anywhere in the comment DOM, and it does NOT require the comment
to be attributed to our handle or Page.

The two error directions are not symmetric:

- A **false positive** (we think our comment is there when it isn't) settles
  the claim: we never comment on that post again. Cost: one comment we
  could have made, on one post, out of thousands.
- A **false negative** (our comment is there but we don't see it) releases the
  claim: we comment a second time. That is the bug this whole slice exists to
  fix, and it is visible to real users on a real post.

So every ambiguity resolves toward "found". Ownership checks are exactly the
kind of extra condition that produces false negatives — IG renders author
names inconsistently, and FB drops the "comment by" aria-label for Page
identities — so they are gone. Matching an 80-char prefix of our own drafted
text is already a strong enough signal: nobody else writes that sentence.

Inconclusive is a real third state; see `CommentLookup`. A nav timeout means
we learned nothing, and must NOT be treated as "no comment there".

Both functions navigate to the post themselves. That matters: it guarantees
the comment composer is empty, so text still sitting in a contenteditable box
from a failed submit cannot be mistaken for a posted comment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.engagement.adapters.facebook_own_comment_dom import (
    FIND_OWN_COMMENT_FB_JS,
    LOAD_ALL_FB_COMMENTS_JS,
)
from lib.engagement.adapters.instagram_own_comment_dom import (
    FIND_OWN_COMMENT_IG_JS,
    LOAD_ALL_IG_COMMENTS_JS,
)
from lib.engagement.result import CommentLookup

if TYPE_CHECKING:
    from playwright.sync_api import Page

# How much of the drafted comment we match on. Long enough to be unique to us,
# short enough to survive the trailing edits/truncation platforms apply.
MATCH_PREFIX_CHARS: int = 80

# Tombstone copy for a deleted/hidden post. Kept narrow on purpose: this is
# matched against the served HTML, so a broad phrase ("sorry, this page") would
# hit strings inside FB/IG's own script bundles and turn every lookup
# inconclusive. Curly apostrophes are folded to straight ones before matching.
_UNAVAILABLE_MARKERS: tuple[str, ...] = (
    "isn't available",
    "content isn't available",
)


def normalize_for_match(text: str) -> str:
    """Whitespace-collapsed, lowercased, `MATCH_PREFIX_CHARS`-long match key.

    The one place the match shape is defined. The DOM finders apply the same
    normalization to candidate comment text, and the post-submit confirmation
    in `lib/engagement/comment_confirm.py` reuses this helper so "did it land?"
    and "is it already there?" can never disagree about what counts as the same
    comment.
    """
    return " ".join(text.split()).lower()[:MATCH_PREFIX_CHARS]


def find_own_comment_ig(
    page: Page, post_url: str, text: str, *, timeout_ms: int = 30000
) -> CommentLookup:
    """Look at an Instagram post and report whether our comment is on it."""
    return _lookup(
        page,
        post_url,
        text,
        load_js=LOAD_ALL_IG_COMMENTS_JS,
        find_js=FIND_OWN_COMMENT_IG_JS,
        timeout_ms=timeout_ms,
    )


def find_own_comment_fb(
    page: Page, post_url: str, text: str, *, timeout_ms: int = 30000
) -> CommentLookup:
    """Look at a Facebook post and report whether our comment is on it."""
    return _lookup(
        page,
        post_url,
        text,
        load_js=LOAD_ALL_FB_COMMENTS_JS,
        find_js=FIND_OWN_COMMENT_FB_JS,
        timeout_ms=timeout_ms,
    )


def _lookup(
    page: Page,
    post_url: str,
    text: str,
    *,
    load_js: str,
    find_js: str,
    timeout_ms: int,
) -> CommentLookup:
    """Navigate, expand every comment, and count matches of `text`.

    Every failure mode below is `inconclusive`, never `absent`: each one means
    we did not get to read the comment list, not that the comment is missing.
    """
    if not normalize_for_match(text):
        return CommentLookup.inconclusive("empty_text")

    try:
        page.goto(post_url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as exc:
        return CommentLookup.inconclusive(f"nav:{type(exc).__name__}")

    if "login" in (page.url or "").lower():
        return CommentLookup.inconclusive("login_wall")
    if _looks_unavailable(page):
        return CommentLookup.inconclusive("post_unavailable")

    try:
        # `evaluate` awaits the async expander's promise before returning.
        page.evaluate(load_js, text)
        raw = page.evaluate(find_js, text)
    except Exception as exc:
        return CommentLookup.inconclusive(f"evaluate:{type(exc).__name__}")

    count = _count_of(raw)
    if count is None:
        return CommentLookup.inconclusive("unreadable_result")
    return CommentLookup.found(count) if count > 0 else CommentLookup.absent()


def _looks_unavailable(page: Page) -> bool:
    """True when the post page is a "content isn't available" tombstone.

    A body read that itself fails returns False: an unreadable body is not
    evidence the post is gone, and the DOM pass below will surface the real
    problem.
    """
    try:
        body = page.content()
    except Exception:
        return False
    haystack = str(body).lower().replace("’", "'")
    return any(marker in haystack for marker in _UNAVAILABLE_MARKERS)


def _count_of(raw: object) -> int | None:
    """Pull `count` out of the finder's `{"count": n}` payload, or None."""
    if not isinstance(raw, dict):
        return None
    value: object = raw.get("count")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
