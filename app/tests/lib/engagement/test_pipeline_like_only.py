"""Like-only degrade tests for ``run_outbound_scan``.

Split out of ``test_pipeline_inline_comment.py`` (300-line cap). With the
two-stage queue retired, there are exactly two ways a scan likes WITHOUT
commenting — this file locks both:

  1. ``inline_comment=False`` (also the default): explicit like-only mode.
     No draft, no comment, no queue, comment counters stay 0.
  2. ``inline_comment=True`` on an adapter with no ``comment`` method:
     the capability probe degrades the run to like-only with a warning
     instead of crashing.

Fakes + factories live in ``_pipeline_fakes``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from lib.engagement.adapter import SupportsComment
from lib.engagement.adapters.fake import FakeAdapter
from lib.engagement.post import Post
from lib.engagement.result import LikeResult
from tests.lib.engagement._pipeline_fakes import (
    FakeIterateOnceDedup,
    FakeLog,
    make_ig_posts,
    make_src,
    run,
)


def _ig_adapter(n: int = 1) -> FakeAdapter:
    """IG adapter with ``n`` high-score (0.85), question-form posts."""
    return FakeAdapter("instagram", [make_src("s1")], {"s1": make_ig_posts(n)})


def _events(log: FakeLog) -> list[str]:
    """Event names (first token) of every log line, in order."""
    return [msg.split(" ", 1)[0] for _level, msg in log.calls]


# --- 1. adapters that cannot comment -----------------------------------------


class _CommentlessAdapter:
    """OutboundAdapter WITHOUT `comment` — what `FacebookGroupAdapter` was
    before the single-pass cutover gave it one."""

    platform = "instagram"

    def __init__(self, posts: list[Post]) -> None:
        self._posts = posts
        self.likes: list[Post] = []

    @contextmanager
    def session(self) -> Iterator[None]:
        yield

    def list_sources(self) -> list[object]:
        return [make_src("s1")]

    def iterate_posts(self, source: object) -> Iterator[Post]:
        yield from self._posts

    def pre_filter(self, post: Post) -> str | None:
        return None

    def adjust_score(self, post: Post, base: float) -> float:
        return base

    def like(self, post: Post) -> LikeResult:
        self.likes.append(post)
        return LikeResult.ok()


def test_commentless_adapter_is_not_treated_as_a_commenter() -> None:
    """The capability probe must reject an adapter with no `comment`."""
    assert not isinstance(_CommentlessAdapter([]), SupportsComment)


def test_inline_comment_degrades_to_like_only_without_capability() -> None:
    """Asking for inline comments on a commentless adapter warns, not crashes."""
    adapter = _CommentlessAdapter(make_ig_posts(1))
    log = FakeLog()
    report, _d, _rt, drafter = run(
        adapter,  # type: ignore[arg-type]
        dedup=FakeIterateOnceDedup(),
        log=log,
        inline_comment=True,
    )

    assert "inline_comment_unavailable" in _events(log)
    assert report.comments_posted == 0
    assert drafter.calls == []
    assert len(adapter.likes) == 1, "the like path still ran"


# --- 2. inline_comment=False is a like-only degrade --------------------------


def test_inline_comment_false_likes_but_never_comments() -> None:
    """``inline_comment=False`` means like-only: no draft, no comment, no queue."""
    adapter = _ig_adapter(3)
    dedup = FakeIterateOnceDedup()
    report, _d, rt, drafter = run(adapter, dedup=dedup, inline_comment=False)

    assert [p.post_id for p in adapter.likes_succeeded] == ["p0", "p1", "p2"]
    assert adapter.comments == [], "like-only mode commented inline"
    assert drafter.calls == [], "like-only mode must not draft"
    assert ("instagram", "comment") not in rt.recorded
    assert [e for e in dedup.engaged if e[2] == "comment"] == []
    assert report.queued == 0


def test_like_only_mode_reports_zero_comment_counters() -> None:
    """The comment counters stay 0 on the like-only path."""
    report, _d, _rt, _dr = run(_ig_adapter(3), inline_comment=False)

    assert report.comments_attempted == 0
    assert report.comments_posted == 0
    assert report.comments_declined == 0


def test_like_only_is_the_default() -> None:
    """Omitting `inline_comment` must not silently start commenting."""
    adapter = _ig_adapter(2)
    run(adapter)

    assert adapter.comments == []
    assert len(adapter.likes_succeeded) == 2
