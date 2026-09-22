"""Structural protocols for `run_outbound_scan`'s collaborators.

Deliberately structural (`Protocol`, not ABC) so the production singleton
modules satisfy the shape without wrapping: `lib.rate_limiter`,
`lib.deduplication` and `lib.draft_helper` are passed as modules.

Split out of `pipeline.py` to keep every engagement module under the
300-line cap. The pipeline re-exports these under their historical
underscore-prefixed names so existing imports keep working.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib.engagement.adapter import Source
from lib.engagement.post import Post


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


@runtime_checkable
class SupportsCommentClaim(Protocol):
    """Optional dedup capability: RESERVE a post before commenting on it.

    Every other action in this pipeline is either idempotent or verifiable at
    submit time, so an after-the-fact mark is enough for them: a repeat like
    comes back `skipped:already_liked`, and a repeat scan just re-reads a page.
    The comment is neither. `lib/ig/comment_post.py:66-85` clicks Post, sleeps,
    and returns True unconditionally, and any exception raised *after* that
    click is flattened into a failure by
    `lib/engagement/adapters/instagram.py:237-238` — so a "failed" comment may
    well be sitting on the post right now. Nothing observed after the fact can
    tell the two apart, which is why this capability hands out a
    **reservation** taken *before* the submit instead of a mark written after
    it. `claim_comment` returning False means "someone already owns this post"
    and the comment must be abandoned, not retried.

    Probed with `isinstance`, exactly like `SupportsMarkSeen` — but the
    fallback is the OPPOSITE. A missing `mark_seen` degrades open (worst case:
    we re-open a post). A missing claim capability degrades CLOSED: a
    collaborator that cannot reserve does not get to comment at all, because
    the failure it would otherwise cause is a duplicate comment on a
    stranger's post, which is user-visible and needs manual cleanup.

    `claims_available` is a separate, cheap question asked *before* the LLM
    draft: it reports whether reservations can still be recorded at all, so an
    outage refuses the work before we pay for a draft the claim will reject.
    """

    def claims_available(self) -> bool: ...
    def claim_comment(
        self,
        platform: str,
        post_id: str,
        *,
        post_url: str = ...,
        target_name: str = ...,
        content: str = ...,
    ) -> bool: ...
    def settle_comment(self, platform: str, post_id: str) -> bool: ...


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


class CommentGate(Protocol):
    """Pre-comment veto consulted before the quota/draft/post steps.

    `check` returns None to allow the comment, or a short skip-reason
    string (e.g. "first_comment_approval_pending") to suppress it. The
    like step is never gated — only the comment is withheld.
    """

    def check(self, post: Post, source: Source) -> str | None: ...


class Log(Protocol):
    """The subset of `logging.Logger` the pipeline uses."""

    def info(self, msg: str, /, *args: object, **kwargs: object) -> None: ...
    def warning(self, msg: str, /, *args: object, **kwargs: object) -> None: ...
