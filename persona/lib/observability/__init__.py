"""Structured logging + correlation IDs for production observability.

Public surface:
    - `configure_logging(level)` — call once at process start
    - `get_logger(name)` — module-level logger factory
    - `correlation_id` — ContextVar carrying the current run's ID
    - `set_correlation_id(value)` / `new_correlation_id(skill, ...)` — set context
    - `with_correlation_id(value)` — context manager for scoped IDs

Usage:
    from lib.observability import configure_logging, get_logger, new_correlation_id

    configure_logging()
    log = get_logger(__name__)

    with new_correlation_id(skill="comment-poster", item_id="fb:abc123"):
        log.info("posting_started", platform="facebook")
        # ... work ...
        log.info("posting_finished", platform="facebook", duration_ms=1234)
"""

from lib.observability.correlation_id import (
    correlation_id,
    new_correlation_id,
    set_correlation_id,
    with_correlation_id,
)
from lib.observability.logger import configure_logging, get_logger

__all__ = [
    "configure_logging",
    "correlation_id",
    "get_logger",
    "new_correlation_id",
    "set_correlation_id",
    "with_correlation_id",
]
