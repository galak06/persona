"""Structural protocols for `run_outbound_scan`'s collaborators.

Deliberately structural (`Protocol`, not ABC) so the production singleton
modules satisfy the shape without wrapping: `rate_limiter`, `deduplication`
and `draft_helper` are passed as bare modules.

Split out of `pipeline.py` to keep every engagement module under the
300-line cap. The pipeline re-exports these under their historical
underscore-prefixed names so existing imports keep working.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class Dedup(Protocol):
    """Records which posts have already been engaged with."""

    def is_duplicate(self, platform: str, post_id: str) -> bool: ...
    def mark_engaged(
        self,
        platform: str,
        post_id: str,
        action: str,
        group_or_hashtag: str = ...,
        status: str = ...,
    ) -> None: ...


@runtime_checkable
class SupportsMarkSeen(Protocol):
    """Optional dedup capability: record that a post was OPENED.

    Distinct from `mark_engaged`, which records an *action* (like, comment).
    `mark_seen` implements iterate-once: a post we opened and reached a
    terminal decision on must never be opened again, so it has to land in
    whatever store `is_duplicate` reads. Probed with `isinstance` so
    collaborators without it (the bare `deduplication` module, used by
    Facebook) keep today's behavior — see `lib/scan_dedup.py` for the
    Instagram implementation.
    """

    def mark_seen(self, platform: str, post_id: str) -> None: ...


class RateTracker(Protocol):
    """Enforces the per-platform daily action budgets."""

    def can_act(self, platform: str, action: str) -> bool: ...
    def record_action(self, platform: str, action: str) -> int: ...
    def wait_random_delay(self, platform: str, action: str) -> None: ...


class Drafter(Protocol):
    """Produces comment text, or "" when the agent declines to engage."""

    def draft_comment_for_post(
        self,
        *,
        platform: str,
        post_text: str,
        group_or_hashtag: str | None,
        post_url: str,
    ) -> str: ...


class QueueIO(Protocol):
    """The two-stage comment queue (Facebook only)."""

    def append(self, record: dict[str, object]) -> None: ...
    def save(self) -> None: ...
    def existing_today(self, platform: str) -> int: ...


class Log(Protocol):
    """The subset of `logging.Logger` the pipeline uses."""

    def info(self, msg: str, /, *args: object, **kwargs: object) -> None: ...
    def warning(self, msg: str, /, *args: object, **kwargs: object) -> None: ...
