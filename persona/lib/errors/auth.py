"""Authentication / authorization errors.

`AuthError` is permanent (bad creds — fix the config).
`TokenExpiredError` is retryable IF the caller knows how to refresh
(IG long-lived tokens, Pinterest OAuth refresh flow).
"""

from __future__ import annotations

from lib.errors.base import PermanentError, RetryableError


class AuthError(PermanentError):
    """401/403 from a platform with no recovery path.

    Raise when the credential is structurally invalid (bad password,
    revoked token, insufficient scope). Surface to user — fix and
    restart. Never auto-retry an AuthError.
    """


class TokenExpiredError(RetryableError):
    """Token expired but refreshable — caller should refresh and retry.

    Raise when the platform returns an OAuth-error response that
    indicates expiry rather than invalidity. Caller is expected to
    invoke the platform-specific refresh flow (e.g.
    `pinterest_auth.refresh_token`) and retry the original call once.
    """
