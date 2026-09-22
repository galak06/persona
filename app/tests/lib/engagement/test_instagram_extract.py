"""Extraction-failure visibility for the Instagram post-detail scrape.

The bug these pin: ``instagram.py`` caught every exception from
``page.evaluate(EXTRACT_POST_DETAILS_JS)`` and substituted
``{"caption": "", ...}`` in silence. An empty caption scores 0.10-0.25 --
below the 0.70 candidate gate AND below the 0.5 near-miss logging floor -- so
a scrape that failed on all 110 posts of a run produced 0 likes, 0 candidates,
0 declines and not one log line, indistinguishable from a quiet day.

So the assertions come in pairs: a FAILED scrape and a genuinely captionless
post must produce the SAME substitute (behaviour is unchanged -- this is
instrumentation, not a fix) and DIFFERENT log events.
"""

from __future__ import annotations

import logging

import pytest
from playwright.sync_api import Error as PlaywrightError

from lib.engagement.adapters.instagram_extract import extract_post_details
from lib.engagement.extraction import (
    EXTRACTION_EMPTY_CAPTION,
    EXTRACTION_FAILED,
    EXTRACTION_OK,
)

_URL = "https://www.instagram.com/p/ABC123/"

#: The exact substitute the pre-instrumentation inline `except` produced.
_LEGACY_SUBSTITUTE = {"caption": "", "like_text": "", "comment_text": "", "author": ""}


class _FakePage:
    """Playwright `Page` stand-in whose `evaluate` returns or raises on demand."""

    def __init__(self, *, result: object = None, raises: BaseException | None = None) -> None:
        self._result = result
        self._raises = raises

    def evaluate(self, _script: str) -> object:
        if self._raises is not None:
            raise self._raises
        return self._result


def _events(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str]]:
    """(levelname, event-name) for every captured line."""
    return [(r.levelname, r.getMessage().split(" ", 1)[0]) for r in caplog.records]


def test_playwright_error_is_logged_and_reported_as_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = _FakePage(raises=PlaywrightError("Execution context was destroyed"))
    with caplog.at_level(logging.INFO):
        details, status = extract_post_details(page, _URL)  # type: ignore[arg-type]

    assert status == EXTRACTION_FAILED
    assert details == _LEGACY_SUBSTITUTE, "the substitute value must not change"
    assert _events(caplog) == [("WARNING", "ig_extract_failed")]
    message = caplog.records[0].getMessage()
    assert "error=Error" in message, "the exception TYPE must be named"
    assert _URL in message, "the post URL must be named"


def test_unexpected_exception_is_still_swallowed_but_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-Playwright error keeps returning the substitute, loudly.

    Narrowing the catch to Playwright errors would turn a scrape bug into an
    aborted run -- a behaviour change. It stays caught; it no longer stays
    quiet, and `reason` marks it as the unexpected arm.
    """
    page = _FakePage(raises=ValueError("boom"))
    with caplog.at_level(logging.INFO):
        details, status = extract_post_details(page, _URL)  # type: ignore[arg-type]

    assert status == EXTRACTION_FAILED
    assert details == _LEGACY_SUBSTITUTE
    assert _events(caplog) == [("WARNING", "ig_extract_failed")]
    assert "reason=unexpected_error" in caplog.records[0].getMessage()
    assert "error=ValueError" in caplog.records[0].getMessage()


def test_null_result_is_reported_as_failed_not_as_an_empty_caption(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`evaluate` returning nothing is a failed scrape, not a captionless post."""
    page = _FakePage(result=None)
    with caplog.at_level(logging.INFO):
        details, status = extract_post_details(page, _URL)  # type: ignore[arg-type]

    assert status == EXTRACTION_FAILED
    assert details == _LEGACY_SUBSTITUTE
    assert _events(caplog) == [("WARNING", "ig_extract_failed")]
    assert "reason=no_result" in caplog.records[0].getMessage()


def test_empty_caption_is_a_different_event_from_a_failed_scrape(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A post that really has no caption: same empty text, different diagnosis."""
    page = _FakePage(
        result={"caption": "   ", "like_text": "42 likes", "comment_text": "3", "author": "someone"}
    )
    with caplog.at_level(logging.INFO):
        details, status = extract_post_details(page, _URL)  # type: ignore[arg-type]

    assert status == EXTRACTION_EMPTY_CAPTION
    assert status != EXTRACTION_FAILED
    assert details["author"] == "someone", "a captionless post keeps its other fields"
    assert details["like_text"] == "42 likes"
    # INFO, not WARNING: a captionless post is ordinary Instagram. The only
    # reason to record it is so it is never mistaken for the failure above.
    assert _events(caplog) == [("INFO", "ig_extract_empty_caption")]


def test_successful_scrape_is_silent_and_reported_ok(
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = _FakePage(
        result={
            "caption": "what do you feed a puppy?",
            "like_text": "12 likes",
            "comment_text": "4 comments",
            "author": "dogperson",
        }
    )
    with caplog.at_level(logging.INFO):
        details, status = extract_post_details(page, _URL)  # type: ignore[arg-type]

    assert status == EXTRACTION_OK
    assert details["caption"] == "what do you feed a puppy?"
    assert caplog.records == [], "a healthy scrape adds no log noise"


def test_missing_and_null_fields_normalize_exactly_as_before() -> None:
    """The old code read the map with `.get(key) or ""`; so does this one."""
    page = _FakePage(result={"caption": "food?", "author": None})
    details, status = extract_post_details(page, _URL)  # type: ignore[arg-type]

    assert status == EXTRACTION_OK
    assert details == {
        "caption": "food?",
        "like_text": "",
        "comment_text": "",
        "author": "",
    }
