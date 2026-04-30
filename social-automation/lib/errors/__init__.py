"""Typed exception hierarchy for social-automation.

Every operation that can fail in production raises one of these. The
categorical bases (`RetryableError`, `PermanentError`, `BugError`) let
callers decide retry vs. escalate vs. crash without inspecting the
specific subclass.

Design rules:
    - Every raised exception MUST be a subclass of `SocialAutomationError`.
    - Use `RetryableError` for transient failures (timeout, 429, 5xx, expired
      token that can be refreshed). Callers may retry with backoff.
    - Use `PermanentError` for failures that won't fix themselves (bad creds,
      content rejected, post deleted). Callers must not retry; surface to user.
    - Use `BugError` for invariant violations — a bug to fix in code, not data.
    - Never raise bare `Exception`, `ValueError`, or `RuntimeError` from
      production paths. Wrap them at the boundary.

Public surface (re-exported):
    Categories:    SocialAutomationError, RetryableError, PermanentError, BugError
    Configuration: ConfigurationError
    Network:       NetworkError
    Auth:          AuthError, TokenExpiredError
    Rate limit:    RateLimitedError
    Validation:    ValidationFailedError
    Posting:       PostFailedError, NotFoundError
"""

from lib.errors.auth import AuthError, TokenExpiredError
from lib.errors.base import (
    BugError,
    PermanentError,
    RetryableError,
    SocialAutomationError,
)
from lib.errors.configuration import ConfigurationError
from lib.errors.network import NetworkError
from lib.errors.posting import NotFoundError, PostFailedError
from lib.errors.rate_limit import RateLimitedError
from lib.errors.validation import ValidationFailedError

__all__ = [
    "AuthError",
    "BugError",
    "ConfigurationError",
    "NetworkError",
    "NotFoundError",
    "PermanentError",
    "PostFailedError",
    "RateLimitedError",
    "RetryableError",
    "SocialAutomationError",
    "TokenExpiredError",
    "ValidationFailedError",
]
