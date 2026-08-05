"""Structured-logging configuration using structlog → JSON to stdout.

Production runners write JSON-per-line to stdout; launchd captures
that into log files; ad-hoc analysis is `jq` against those files.

Every log line carries:
    - `timestamp` — ISO 8601 UTC
    - `level` — info | warning | error | debug
    - `correlation_id` — the current run's ID (see correlation_id.py)
    - `event` — the event name (first positional arg to logger calls)
    - any kwargs passed to the call — `platform="fb"`, `duration_ms=123`, etc.

In dev (`PRETTY_LOGS=1`), output switches to a colorized human-readable
format that's easier to scan in a terminal.
"""

from __future__ import annotations

import logging
import os
import sys

import structlog
from structlog.types import EventDict, Processor

from lib.observability.correlation_id import correlation_id


def _add_correlation_id(_logger: object, _name: str, event_dict: EventDict) -> EventDict:
    """structlog processor that injects the current correlation ID into every log line."""
    event_dict["correlation_id"] = correlation_id.get()
    return event_dict


def configure_logging(
    level: str = "INFO",
    *,
    pretty: bool | None = None,
) -> None:
    """Configure structlog and stdlib logging. Call once at process start.

    Idempotent — calling twice is harmless. The pretty/JSON choice is
    sticky for the process lifetime.

    Args:
        level: Minimum level to emit (`DEBUG`, `INFO`, `WARNING`, `ERROR`).
        pretty: Force pretty (terminal) output if True, JSON if False.
            Default (None) reads `PRETTY_LOGS` env var — set to truthy
            for pretty output in dev terminals, leave unset for JSON
            in production (launchd / CI).
    """
    if pretty is None:
        pretty = os.environ.get("PRETTY_LOGS", "").lower() in ("1", "true", "yes")

    # Stdlib logging foundation — structlog wraps it.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_correlation_id,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if pretty:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a module-level logger. Cheap — call at module top-level.

    Conventional usage:

        from lib.observability import get_logger
        log = get_logger(__name__)

        def do_thing() -> None:
            log.info("thing_started", target="x")
    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]
