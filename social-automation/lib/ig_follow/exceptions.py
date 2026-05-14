"""Exceptions raised by the IG follow-scout.

Mapped to the project's standard taxonomy in `lib.errors.base`:

    - IGActionBlockedError  -> RetryableError (IG locked us; back off)
    - IGUserNotFoundError   -> PermanentError (handle is dead; don't retry)

Already-following and account-is-private are NOT exceptions — they are
expected outcomes surfaced via the `FollowResult` enum from `follower`.
"""

from __future__ import annotations

from lib.errors.base import PermanentError, RetryableError


class IGActionBlockedError(RetryableError):
    """Instagram blocked the current action.

    Detected when the DOM surfaces phrases like "Try Again Later",
    "Action Blocked", or the well-known challenge dialog. The block
    is typically rate-based and clears within hours-to-days — retry
    on the next scheduled run, not within the same process.

    Callers MUST abort the entire batch on this exception (not just
    skip the current target) — continuing to act while blocked
    escalates the block and risks an account-level action.
    """


class IGUserNotFoundError(PermanentError):
    """Profile URL returned a "Sorry, this page isn't available" page.

    The handle has been renamed, deleted, or never existed. Caller
    should mark the handle in the source list as inactive so future
    scout runs skip it.
    """
