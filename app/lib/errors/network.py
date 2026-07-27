"""Network error — transient connectivity / timeout failures."""

from __future__ import annotations

from lib.errors.base import RetryableError


class NetworkError(RetryableError):
    """Transport-level failure: timeout, DNS, TLS, 5xx response.

    Wrap at the I/O boundary — when an `httpx.ReadTimeout`,
    `httpx.ConnectError`, or HTTP 5xx is observed — to convert library
    exceptions into our taxonomy. Callers retry with backoff.
    """
