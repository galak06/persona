"""Categorical exception bases.

These four bases form the entire taxonomy of failures. Every concrete
exception in `lib.errors.*` inherits from exactly one of:

    - `RetryableError` — transient; caller may retry with backoff
    - `PermanentError` — won't self-heal; surface to user, do not retry
    - `BugError` — invariant violated; fix the code, not the data

`SocialAutomationError` is the umbrella base — catch this at the very
outermost handler (e.g. a runner's `try/except`) to guarantee any error
from this codebase is captured in structured logs.
"""

from __future__ import annotations


class SocialAutomationError(Exception):
    """Base for every exception raised by this codebase.

    Carries an optional `context` dict for structured logging. Always
    prefer raising a concrete subclass — this base exists for `except`
    clauses that need to catch *anything* from our code without
    swallowing stdlib exceptions like KeyboardInterrupt.

    Args:
        message: Human-readable description.
        context: Structured fields to include in logs (correlation_id,
            platform, item_id, etc.). Never put secrets here — context
            is logged verbatim.
    """

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.context: dict[str, object] = dict(context) if context else {}


class RetryableError(SocialAutomationError):
    """Transient failure — caller may retry.

    Includes: network timeouts, 429s, refreshable tokens, browser session
    glitches. Callers should apply bounded backoff (typically 3 attempts
    with exponential delay) before escalating to a `PermanentError`.
    """


class PermanentError(SocialAutomationError):
    """Failure that won't fix itself with retry.

    Includes: invalid credentials, post deleted, content rejected,
    schema mismatches. Callers must not retry; surface to the user
    via Telegram and persist a failure record in the queue.
    """


class BugError(SocialAutomationError):
    """An invariant was violated — fix the code.

    Raise this when reaching a state that should be impossible given
    the design (e.g. queue item with `status="posted"` but no
    `posted_at`). These should crash loudly in dev and page on-call
    in production rather than be silently retried.
    """
