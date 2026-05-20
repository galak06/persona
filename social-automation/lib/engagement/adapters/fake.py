"""FakeAdapter — test double for OutboundAdapter.

Replaces a real Playwright-backed adapter in scanner tests. Constructed with
canned sources + posts. Records every like() call so tests can assert which
posts were engaged with.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from lib.engagement.adapter import Source
from lib.engagement.post import Post
from lib.engagement.result import LikeResult


@dataclass(frozen=True)
class FakeSource:
    """Concrete Source implementation for tests."""
    id: str
    name: str
    url: str


class FakeAdapter:
    """Canned-data adapter. No browser, no network."""

    def __init__(
        self,
        platform: str,
        sources: list[Source],
        posts_by_source: dict[str, list[Post]],
        *,
        like_should_fail: bool = False,
        pre_filter_overrides: dict[str, str] | None = None,
        score_boost: float = 0.0,
    ) -> None:
        self.platform = platform
        self._sources = sources
        self._posts = posts_by_source
        self._like_should_fail = like_should_fail
        self._pre_filter_overrides = pre_filter_overrides or {}
        self._score_boost = score_boost
        self.likes_attempted: list[Post] = []
        self.likes_succeeded: list[Post] = []

    @contextmanager
    def session(self) -> Iterator[None]:
        # No-op session for tests.
        yield

    def list_sources(self) -> list[Source]:
        return list(self._sources)

    def iterate_posts(self, source: Source) -> Iterator[Post]:
        yield from self._posts.get(source.id, [])

    def pre_filter(self, post: Post) -> str | None:
        # Optional per-post-id override for tests that want to verify
        # rejection paths (e.g., simulate "competitor" rejection).
        return self._pre_filter_overrides.get(post.post_id)

    def adjust_score(self, post: Post, base: float) -> float:
        return base + self._score_boost

    def like(self, post: Post) -> LikeResult:
        self.likes_attempted.append(post)
        if self._like_should_fail:
            return LikeResult.failed("fake_failure")
        self.likes_succeeded.append(post)
        return LikeResult.ok()
