"""Reusable checkpoint-validation gate for recipe-pipeline phases.

Every pipeline phase ends by calling :func:`checkpoint` with the invariant it
must satisfy before the next phase runs. A failed checkpoint raises
:class:`CheckpointError` (halting the phase) and emits a structured ``error``
record; a passing one emits a structured ``info`` record.

The logger is injected (any object exposing ``.info``/``.error`` that accepts
keyword fields — e.g. a ``structlog`` bound logger) so this module carries no
cross-package logging dependency. When omitted, a stdlib-backed structured
fallback is used so checkpoints are still JSON-greppable in logs.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol


class StructuredLogger(Protocol):
    """Minimal structured-logging surface used by the checkpoint gate."""

    def info(self, event: str, /, **fields: object) -> None: ...
    def error(self, event: str, /, **fields: object) -> None: ...


class CheckpointError(RuntimeError):
    """Raised when a pipeline phase fails its end-of-phase validation gate."""

    def __init__(self, phase: str, reason: str) -> None:
        self.phase = phase
        self.reason = reason
        super().__init__(f"checkpoint failed [{phase}]: {reason}")


class _StdlibStructured:
    """Fallback logger: stdlib logging with kwargs folded into a JSON message."""

    def __init__(self, name: str) -> None:
        self._log = logging.getLogger(name)

    def info(self, event: str, /, **fields: object) -> None:
        self._log.info(self._fmt(event, fields))

    def error(self, event: str, /, **fields: object) -> None:
        self._log.error(self._fmt(event, fields))

    @staticmethod
    def _fmt(event: str, fields: dict[str, object]) -> str:
        return json.dumps(
            {"event": event, **fields}, default=str, ensure_ascii=False
        )


_DEFAULT_LOGGER: StructuredLogger = _StdlibStructured("recipe_pipeline.checkpoint")


def checkpoint(
    phase: str,
    *,
    ok: bool,
    reason: str = "",
    logger: StructuredLogger | None = None,
    **fields: object,
) -> None:
    """Record a phase checkpoint; raise :class:`CheckpointError` when not ``ok``.

    Args:
        phase: Phase name (e.g. ``"seasonal_selection"``).
        ok: Whether the phase's invariant held.
        reason: Why it failed; surfaced in the log record and the exception.
        logger: Structured logger to emit on. Defaults to a stdlib fallback.
        **fields: Extra structured fields (counts, ids) for the log record.

    Raises:
        CheckpointError: when ``ok`` is False.
    """
    log = logger or _DEFAULT_LOGGER
    if ok:
        log.info("checkpoint_pass", phase=phase, **fields)
        return
    log.error("checkpoint_fail", phase=phase, reason=reason, **fields)
    raise CheckpointError(phase, reason or "invariant not satisfied")
