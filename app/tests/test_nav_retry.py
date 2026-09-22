"""Transient-navigation retry — `lib.engagement.nav_retry`.

What matters here is the boundary: retry the network outages that cost 9 of
28 August `fb-engager` runs, and do NOT retry anything that means a real
problem, which would hide it behind a delay.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from lib.engagement.nav_retry import goto_with_retry, is_transient_nav_error


class _Page:
    """Fails the first `fail_times` gotos with `error`, then succeeds."""

    def __init__(self, error: BaseException, fail_times: int) -> None:
        self._error = error
        self._fail_times = fail_times
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def goto(self, url: str, **kwargs: Any) -> str:
        # the wrapper always passes all three explicitly; record only the ones
        # actually set so assertions stay about intent, not defaults
        self.calls.append((url, {k: v for k, v in kwargs.items() if v is not None}))
        if len(self.calls) <= self._fail_times:
            raise self._error
        return "ok"


def _dns_error() -> PlaywrightError:
    return PlaywrightError("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://www.facebook.com/")


# ─────────────────────────────────────────────── what counts as transient


@pytest.mark.parametrize(
    "message",
    [
        "Page.goto: net::ERR_NAME_NOT_RESOLVED at https://www.facebook.com/",
        "Page.goto: net::ERR_INTERNET_DISCONNECTED",
        "Page.goto: net::ERR_CONNECTION_RESET",
    ],
)
def test_network_errors_are_transient(message: str) -> None:
    assert is_transient_nav_error(PlaywrightError(message))


def test_goto_timeout_is_transient() -> None:
    assert is_transient_nav_error(PlaywrightTimeoutError("Page.goto: Timeout 30000ms exceeded."))


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("SESSION_EXPIRED: Facebook login required"),
        PlaywrightError("Page.click: selector resolved to hidden element"),
        ValueError("nope"),
    ],
)
def test_application_errors_are_not_transient(exc: BaseException) -> None:
    """A dead session or a bad selector is a real problem -- retrying it would
    just delay the report."""
    assert not is_transient_nav_error(exc)


# ─────────────────────────────────────────────────────────── retry behaviour


def test_recovers_when_a_blip_clears() -> None:
    page = _Page(_dns_error(), fail_times=2)
    slept: list[float] = []
    result = goto_with_retry(page, "https://x", sleep=slept.append)  # type: ignore[arg-type]
    assert result == "ok"
    assert len(page.calls) == 3
    assert slept == [2.0, 4.0]  # linear backoff, not exponential


def test_first_attempt_success_never_sleeps() -> None:
    page = _Page(_dns_error(), fail_times=0)
    slept: list[float] = []
    result = goto_with_retry(page, "https://x", sleep=slept.append)  # type: ignore[arg-type]
    assert result == "ok"
    assert len(page.calls) == 1
    assert slept == []


def test_reraises_after_exhausting_attempts() -> None:
    page = _Page(_dns_error(), fail_times=99)
    with pytest.raises(PlaywrightError, match="ERR_NAME_NOT_RESOLVED"):
        goto_with_retry(page, "https://x", attempts=3, sleep=lambda _s: None)  # type: ignore[arg-type]
    assert len(page.calls) == 3


def test_non_transient_error_raises_immediately_without_retrying() -> None:
    """The whole point of the narrow filter: a real failure keeps its original
    call site and costs no extra wall-clock."""
    page = _Page(RuntimeError("SESSION_EXPIRED"), fail_times=99)
    slept: list[float] = []
    with pytest.raises(RuntimeError, match="SESSION_EXPIRED"):
        goto_with_retry(page, "https://x", sleep=slept.append)  # type: ignore[arg-type]
    assert len(page.calls) == 1
    assert slept == []


def test_goto_kwargs_are_passed_through_on_every_attempt() -> None:
    page = _Page(_dns_error(), fail_times=1)
    goto_with_retry(
        page, "https://x", wait_until="domcontentloaded", timeout=30000, sleep=lambda _s: None
    )
    assert all(
        kwargs == {"wait_until": "domcontentloaded", "timeout": 30000}
        for _url, kwargs in page.calls
    )


def test_zero_attempts_is_rejected() -> None:
    with pytest.raises(ValueError):
        goto_with_retry(_Page(_dns_error(), 0), "https://x", attempts=0)  # type: ignore[arg-type]
