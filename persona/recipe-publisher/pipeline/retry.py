# pyright: reportMissingImports=false
"""Phase 9 helper: retry loop for transient publish failures.

A small, pure, dependency-injected retry primitive used by the publishing
phase. Retries ``fn`` on *transient* errors up to ``attempts`` times; a
non-transient error propagates immediately; exhausting all attempts raises
``RetryExhaustedError`` carrying the attempt count and the last error so the caller
can record the outcome.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pipeline.checkpoint import StructuredLogger

T = TypeVar("T")


class RetryExhaustedError(RuntimeError):
    """Raised when all retry attempts fail on transient errors."""

    def __init__(self, attempts: int, last: BaseException) -> None:
        self.attempts = attempts
        self.last = last
        super().__init__(f"failed after {attempts} attempts: {last!r}")


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    is_transient: Callable[[BaseException], bool] | None = None,
    logger: StructuredLogger | None = None,
) -> tuple[T, int]:
    """Call ``fn`` with retry. Returns ``(result, attempts_used)``.

    Raises the original error immediately if it is non-transient, or
    ``RetryExhaustedError`` once ``attempts`` transient failures have occurred.
    """
    transient = is_transient or (lambda _exc: True)
    for attempt in range(1, attempts + 1):
        try:
            return fn(), attempt
        except Exception as exc:
            if not transient(exc):
                raise
            if logger is not None:
                logger.info(
                    "publish_retry", attempt=attempt, error=type(exc).__name__
                )
            if attempt == attempts:
                raise RetryExhaustedError(attempt, exc) from exc
    raise AssertionError("unreachable")  # pragma: no cover
