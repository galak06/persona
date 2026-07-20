"""OutboundAdapter Protocol — platform-specific seam for OutboundEngagement.

Today implemented by FacebookGroupAdapter and InstagramHashtagAdapter (production)
and FakeAdapter (tests). Each adapter owns its session/auth, DOM selectors,
platform-specific pre-filters, score adjustments, and the inline like action.

Scanner orchestration (loop over sources, score, dedup, draft, queue write)
stays in scripts/fb_scan.py and scripts/ig_scan.py at slice 2; the pipeline
function arrives in slice 3.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Protocol, runtime_checkable

from lib.engagement.post import Post
from lib.engagement.result import CommentResult, LikeResult


@runtime_checkable
class Source(Protocol):
    """A scan source: an FB group or an IG hashtag."""
    id: str
    name: str
    url: str


@runtime_checkable
class OutboundAdapter(Protocol):
    platform: str  # "facebook" | "instagram"

    def session(self) -> AbstractContextManager[None]:
        """Open + tear down the platform session (browser launch, login check)."""
        ...

    def list_sources(self) -> list[Source]:
        """Groups (FB) or hashtags (IG) to iterate this run."""
        ...

    def iterate_posts(self, source: Source) -> Iterator[Post]:
        """Yield normalized Post objects from one source."""
        ...

    def pre_filter(self, post: Post) -> str | None:
        """Return a rejection reason if this post should be skipped, else None.

        Examples: "competitor", "own_account", "too_old".
        FB today returns None for all posts.
        """
        ...

    def adjust_score(self, post: Post, base: float) -> float:
        """Apply platform-specific score adjustment on top of base relevance.

        FB returns base unchanged. IG boosts <500 likes and penalizes >5k.
        """
        ...

    def like(self, post: Post) -> LikeResult:
        """Perform the inline like action.

        IG: clicks the like button. FB: returns LikeResult.skipped("not_supported")
        until slice 4 adds Page-as-actor liking.
        """
        ...


@runtime_checkable
class SupportsComment(Protocol):
    """Optional capability: post a comment during the scan visit.

    Deliberately NOT part of `OutboundAdapter` — Facebook drafts and posts in
    a separate stage (`scripts/fb_comment.py`), so `FacebookGroupAdapter` has
    no `comment` and must stay a valid `OutboundAdapter`. The pipeline probes
    this with `isinstance(adapter, SupportsComment)` and only comments inline
    when the adapter opts in by implementing the method.
    """

    def comment(self, post: Post, text: str) -> CommentResult:
        """Submit `text` as a comment on `post` in the current session."""
        ...
