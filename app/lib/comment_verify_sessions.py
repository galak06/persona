"""Per-platform browser plumbing for the comment-claim reconciler.

`scripts/comment_verify.py` owns the decision — settle a claim, release it, or
leave it alone. This module owns the only two things that differ between
platforms while that decision is being made: how to obtain an authenticated
page, and which DOM matcher reads that platform's comment list.

Split out of the script to keep it under the 300-line cap, and because the
adjudication table is much easier to read without a Playwright lifecycle
wrapped around it.

Neither factory invents a session. Instagram reuses the adapter's own
`InstagramSession` lifecycle and Facebook the brand-scoped `build_fb_session`
already shared by five FB scripts, so the reconciler authenticates in exactly
the way the engagers it reconciles do. Both raise on an expired session; the
caller treats that as "skip this platform", never as "these comments did not
land".
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING

from lib.config import settings
from lib.engagement.adapters.instagram_session import InstagramSession
from lib.engagement.adapters.own_comment import find_own_comment_fb, find_own_comment_ig
from lib.engagement.result import CommentLookup
from lib.fb.session import build_fb_session

if TYPE_CHECKING:
    from playwright.sync_api import Page

LookupFn = Callable[["Page", str, str], CommentLookup]
PageFactory = Callable[[], AbstractContextManager["Page"]]


@contextmanager
def instagram_page() -> Iterator[Page]:
    """An authenticated IG page, via the adapter's own session lifecycle."""
    session = InstagramSession(settings.paths.instagram_session)
    with session.open():
        yield session.require_page()


@contextmanager
def facebook_page() -> Iterator[Page]:
    """An authenticated FB page, via the brand-scoped session factory."""
    with build_fb_session().page() as page:
        yield page


PLATFORM_SESSIONS: dict[str, tuple[PageFactory, LookupFn]] = {
    "instagram": (instagram_page, find_own_comment_ig),
    "facebook": (facebook_page, find_own_comment_fb),
}
