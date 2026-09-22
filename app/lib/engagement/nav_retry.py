"""Retry a Playwright navigation that failed for a transient network reason.

Every engager run opens with a bare `page.goto(...)` to the platform's home
page to validate the saved session. That single call had no retry, so one
momentary DNS blip inside the worker container raised straight out of
`session()` and exited the whole run -- and because these are daily crons,
a blip lasting seconds cost a full day of engagement. Across August 2026 that
was 9 of 28 `fb-engager` runs.

The failures are genuinely transient, not the platform refusing us: in the
2026-08-26 run `www.facebook.com` AND `api.telegram.org` both failed to
resolve in the same run, which is the container's embedded resolver losing
its upstream (typically around a host sleep/wake or network change), not a
block.

Deliberately narrow: only network-layer navigation failures are retried. A
`SESSION_EXPIRED` login redirect, a selector timeout, or any application-level
error is NOT transient -- retrying those would just burn time and hammer the
platform while hiding a real problem.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Response
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from lib.observability import get_logger

logger = get_logger(__name__)

_WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]

DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 2.0

# Chromium net-stack errors that mean "the network wasn't there", all of which
# routinely clear within seconds. `ERR_NAME_NOT_RESOLVED` is the one actually
# observed in production; the rest are its close neighbours and are included
# so a slightly different flavour of the same outage doesn't fall through.
_TRANSIENT_MARKERS = (
    "ERR_NAME_NOT_RESOLVED",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_NETWORK_CHANGED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_CONNECTION_FAILED",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_CONNECTION_REFUSED",
    "ERR_ADDRESS_UNREACHABLE",
    "ERR_TIMED_OUT",
    "ERR_SOCKET_NOT_CONNECTED",
    "ERR_EMPTY_RESPONSE",
)


def is_transient_nav_error(exc: BaseException) -> bool:
    """Whether `exc` is a navigation failure worth retrying.

    A bare `TimeoutError` from `goto` counts: a page that never reached
    `domcontentloaded` is usually the same underlying outage, and the caller's
    own timeout already bounds how long that costs.
    """
    if isinstance(exc, PlaywrightTimeoutError):
        return True
    if not isinstance(exc, PlaywrightError):
        return False
    message = str(exc)
    return any(marker in message for marker in _TRANSIENT_MARKERS)


def goto_with_retry(
    page: Page,
    url: str,
    *,
    wait_until: _WaitUntil | None = None,
    timeout: float | None = None,
    referer: str | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> Response | None:
    """`page.goto(...)`, retried on transient network errors.

    The navigation parameters mirror Playwright's own `Page.goto` rather than
    being forwarded as `**kwargs`: `goto` takes explicit keyword arguments, so
    a passthrough signature could not be type-checked against it.

    Backs off linearly (`base_delay`, `2*base_delay`, ...) -- these outages
    clear in seconds, so an exponential curve would mostly just idle.

    Re-raises the last error once `attempts` is exhausted, and re-raises
    IMMEDIATELY for anything not transient, so a real failure still surfaces
    at its original call site with its original traceback.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return page.goto(url, wait_until=wait_until, timeout=timeout, referer=referer)
        except Exception as exc:
            if not is_transient_nav_error(exc):
                raise
            last_error = exc
            if attempt == attempts:
                break
            delay = base_delay * attempt
            logger.warning(
                "nav_retry_transient_failure",
                url=url,
                attempt=attempt,
                attempts=attempts,
                retry_in_seconds=delay,
                error=str(exc)[:200],
            )
            sleep(delay)

    logger.error("nav_retry_exhausted", url=url, attempts=attempts, error=str(last_error)[:200])
    assert last_error is not None  # loop only breaks after setting it
    raise last_error
