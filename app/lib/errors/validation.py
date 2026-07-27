"""Validation error — generated content failed brand-voice / schema rules."""

from __future__ import annotations

from collections.abc import Sequence

from lib.errors.base import PermanentError


class ValidationFailedError(PermanentError):
    """Generated content failed validation after all retries.

    Raise when the regenerate-with-feedback loop exhausts its retry
    budget and the draft still violates brand-voice or schema rules.
    `violations` carries the list of rule names that failed so the
    runner can log a diagnostic and surface to user via Telegram.

    This is permanent in the sense that retrying with the same prompt
    won't help — the content needs human revision or the rules need
    updating.
    """

    def __init__(
        self,
        message: str,
        *,
        violations: Sequence[str],
        context: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, context=context)
        self.violations: list[str] = list(violations)
