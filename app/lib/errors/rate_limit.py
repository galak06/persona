"""Rate-limit error — caller exceeded our internal cap or platform's."""

from __future__ import annotations

from lib.errors.base import RetryableError


class RateLimitedError(RetryableError):
    """Internal or platform rate limit reached.

    Raise when:
        - our `lib.rate_limiter.can_act()` returns False
        - a platform returns 429
        - a platform-specific cap is hit (e.g. IG container creation)

    Retryable in principle, but typically the right behavior is to
    abort the current run and retry on the next cron cycle — not
    busy-loop. The `retry_after_seconds` hint, when present, tells
    the scheduler when retry is safe.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: int | None = None,
        context: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, context=context)
        self.retry_after_seconds: int | None = retry_after_seconds
