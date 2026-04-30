"""Correlation IDs — propagate a single ID across all logs of one run.

Every runner generates a correlation ID at start; every log line in
that run carries it. Lets you grep `correlation_id="comment-poster:2026-04-30:fb-abc"`
across stdout/journald and reconstruct the full timeline of one run
even when many runs interleave.

Stored in a `ContextVar` so it's:
    - thread-safe (each thread gets its own slot)
    - async-safe (each task inherits its parent's slot)
    - automatically cleared on context exit

Format is human-readable (`<skill>:<date>:<item>`) rather than UUID
because we grep these in terminal logs daily.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

# Sentinel "<unset>" lets every log line carry SOMETHING — we never
# want a missing field to crash the JSON formatter.
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="<unset>")


def set_correlation_id(value: str) -> None:
    """Set the current run's correlation ID. Persists for the rest of the
    context (thread/task) until overwritten or `with_correlation_id` exits.
    """
    correlation_id.set(value)


def new_correlation_id(
    skill: str,
    *,
    item_id: str | None = None,
    when: datetime | None = None,
) -> _CorrelationContext:
    """Generate a new correlation ID and enter a context manager that holds it.

    The ID format is `<skill>:<YYYY-MM-DD>:<item_id>` (item_id omitted if None).
    Use as `with new_correlation_id("comment-poster", item_id="fb-abc"): ...`.

    Args:
        skill: The skill or runner name (e.g. "comment-poster", "recipe-publisher").
        item_id: Optional per-item identifier (queue post id, recipe slug, etc.).
        when: Override the date component (mostly for tests). UTC.

    Returns:
        A context manager that sets the ID on enter and restores the prior
        value on exit. Reset behavior survives exceptions.
    """
    now = when or datetime.now(UTC)
    parts = [skill, now.strftime("%Y-%m-%d")]
    if item_id:
        parts.append(item_id)
    return _CorrelationContext(":".join(parts))


@contextmanager
def with_correlation_id(value: str) -> Iterator[str]:
    """Context manager: set `correlation_id` to `value`, restore on exit.

    Useful when you already have a stable ID (e.g. resuming a graph
    thread) and want it scoped to a block:

        with with_correlation_id("comment-poster:2026-04-30:fb-abc"):
            log.info("event")
    """
    token = correlation_id.set(value)
    try:
        yield value
    finally:
        correlation_id.reset(token)


class _CorrelationContext:
    """Internal helper for `new_correlation_id`. Same shape as
    `with_correlation_id` but pre-formatted; can also expose `.value`
    when callers need the ID string outside the with-block."""

    def __init__(self, value: str) -> None:
        self.value: str = value
        self._token: object | None = None

    def __enter__(self) -> str:
        self._token = correlation_id.set(self.value)
        return self.value

    def __exit__(self, *_args: object) -> None:
        if self._token is not None:
            correlation_id.reset(self._token)  # type: ignore[arg-type]
            self._token = None
