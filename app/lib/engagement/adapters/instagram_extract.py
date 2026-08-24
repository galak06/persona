"""Post-detail extraction for the Instagram adapter, with failure visibility.

`EXTRACT_POST_DETAILS_JS` runs inside the post page and returns the caption,
the author handle and the raw like/comment count text. It can come back three
ways, and the point of this module is that each one is DISTINGUISHABLE
afterwards:

- the evaluate raised (navigation raced it, the page was torn down, IG shipped
  a DOM change the selectors no longer match) -> `EXTRACTION_FAILED`
- the evaluate returned nothing usable (`null`, or a non-object) ->
  `EXTRACTION_FAILED`
- the evaluate returned a real object whose caption is empty -> the post
  genuinely has no caption -> `EXTRACTION_EMPTY_CAPTION`

Before this module, `instagram.py` caught the raise and substituted
`{"caption": "", ...}` without a word in the log. An empty caption scores
0.10-0.25, which is under the 0.70 candidate gate AND under the 0.5 near-miss
logging floor -- so a scrape that failed on every post in the run looked
exactly like a run of uninteresting posts: 0 likes, 0 candidates, 0 declines,
0 warnings. Every path below logs, and the caller stamps the returned status
onto the `Post` so the run summary can count it.

The substitute value itself is unchanged, deliberately: this module is
instrumentation, not a fix, and a post whose caption failed to scrape must go
on being scored exactly as it was before.
"""

from __future__ import annotations

import logging

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from lib.engagement.adapters.instagram_dom import EXTRACT_POST_DETAILS_JS
from lib.engagement.extraction import (
    EXTRACTION_EMPTY_CAPTION,
    EXTRACTION_FAILED,
    EXTRACTION_OK,
)

_log = logging.getLogger(__name__)

_PW_ERRORS: tuple[type[BaseException], ...] = (PlaywrightError, PlaywrightTimeoutError)

#: The keys `InstagramHashtagAdapter.iterate_posts` reads off the result.
_DETAIL_KEYS = ("caption", "like_text", "comment_text", "author")


def _empty_details() -> dict[str, str]:
    """The substitute detail map used whenever extraction fails."""
    return dict.fromkeys(_DETAIL_KEYS, "")


def extract_post_details(page: Page, post_url: str) -> tuple[dict[str, str], str]:
    """Scrape one post's details; return `(details, extraction_status)`.

    `details` always carries every key in `_DETAIL_KEYS`, so the caller reads
    it the same way whatever happened. The status is one of the
    `lib.engagement.extraction` constants and is what makes a failed scrape
    tellable from a genuinely captionless post afterwards.
    """
    try:
        raw = page.evaluate(EXTRACT_POST_DETAILS_JS)
    except _PW_ERRORS as exc:
        _log.warning(
            "ig_extract_failed reason=evaluate_raised error=%s post_url=%s",
            type(exc).__name__,
            post_url,
        )
        return _empty_details(), EXTRACTION_FAILED
    except Exception as exc:
        # Kept as a catch-all ONLY because the code this replaced swallowed
        # everything: narrowing it to Playwright errors would turn a scrape
        # bug into an aborted run, which is a behaviour change. It is now
        # loud, and its `reason` marks it as the unexpected arm.
        _log.warning(
            "ig_extract_failed reason=unexpected_error error=%s post_url=%s",
            type(exc).__name__,
            post_url,
        )
        return _empty_details(), EXTRACTION_FAILED

    if not isinstance(raw, dict):
        _log.warning(
            "ig_extract_failed reason=no_result result_type=%s post_url=%s",
            type(raw).__name__,
            post_url,
        )
        return _empty_details(), EXTRACTION_FAILED

    details = {key: str(raw.get(key) or "") for key in _DETAIL_KEYS}
    if not details["caption"].strip():
        # Not a warning: a captionless post is ordinary Instagram, and the
        # only reason to record it is so it never gets mistaken for the
        # failure above when the candidate count goes to zero.
        _log.info(
            "ig_extract_empty_caption post_url=%s author=%s",
            post_url,
            details["author"],
        )
        return details, EXTRACTION_EMPTY_CAPTION
    return details, EXTRACTION_OK
