"""Errors raised by the publish/post step (vs. drafting / validating)."""

from __future__ import annotations

from lib.errors.base import PermanentError


class PostFailedError(PermanentError):
    """Platform rejected or timed-out the post operation.

    Raise after exhausting platform-specific retries when the post
    cannot be confirmed as published (e.g. FB comment-box not found,
    IG container stuck in `IN_PROGRESS`, WP REST returns 4xx).

    Carries the platform name so the runner's structured log gets a
    clean `platform=facebook` field instead of parsing the message.
    """

    def __init__(
        self,
        message: str,
        *,
        platform: str,
        context: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, context=context)
        self.platform: str = platform


class NotFoundError(PermanentError):
    """Target resource does not exist (post deleted, group gone, 404).

    Raise when navigating to a queue item's URL returns 404, the post
    is hidden, or the group has been removed. The runner marks the
    queue item POST_UNAVAILABLE and moves on — never retried.
    """
