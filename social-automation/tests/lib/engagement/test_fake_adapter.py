"""Behavior tests for FakeAdapter.

FakeAdapter is the test double for the OutboundAdapter Protocol. Verifies the
canned-data plumbing (sources, posts, like recording) the scanner tests rely on.
"""

from __future__ import annotations

from lib.engagement.adapters.fake import FakeAdapter, FakeSource
from lib.engagement.post import Post
from lib.engagement.result import LikeResult


def _src(sid: str = "g1") -> FakeSource:
    return FakeSource(id=sid, name=f"name-{sid}", url=f"https://x/{sid}")


def _post(pid: str, source_id: str = "g1") -> Post:
    return Post(
        platform="facebook",
        post_id=pid,
        post_url=f"https://x/p/{pid}",
        text=f"text {pid}",
        source_id=source_id,
        source_name="g1-name",
        source_url="https://x/g1",
    )


def test_list_sources_returns_canned() -> None:
    s1, s2 = _src("a"), _src("b")
    fa = FakeAdapter("facebook", [s1, s2], {})
    assert fa.list_sources() == [s1, s2]


def test_iterate_posts_yields_canned_for_source_id() -> None:
    s = _src("g1")
    p1, p2 = _post("1"), _post("2")
    fa = FakeAdapter("facebook", [s], {"g1": [p1, p2]})
    assert list(fa.iterate_posts(s)) == [p1, p2]


def test_iterate_posts_unknown_source_yields_nothing() -> None:
    s = _src("missing")
    fa = FakeAdapter("facebook", [s], {"g1": [_post("1")]})
    assert list(fa.iterate_posts(s)) == []


def test_pre_filter_default_returns_none() -> None:
    fa = FakeAdapter("facebook", [], {})
    assert fa.pre_filter(_post("1")) is None


def test_pre_filter_override_returns_reason() -> None:
    fa = FakeAdapter("facebook", [], {}, pre_filter_overrides={"1": "competitor"})
    assert fa.pre_filter(_post("1")) == "competitor"
    assert fa.pre_filter(_post("2")) is None


def test_adjust_score_applies_boost() -> None:
    fa = FakeAdapter("instagram", [], {}, score_boost=0.15)
    assert fa.adjust_score(_post("1"), 0.50) == 0.65


def test_adjust_score_default_no_boost() -> None:
    fa = FakeAdapter("instagram", [], {})
    assert fa.adjust_score(_post("1"), 0.50) == 0.50


def test_like_success_records_attempted_and_succeeded() -> None:
    fa = FakeAdapter("instagram", [], {})
    p = _post("1")
    result: LikeResult = fa.like(p)
    assert result == LikeResult.ok()
    assert fa.likes_attempted == [p]
    assert fa.likes_succeeded == [p]


def test_like_failure_records_only_attempted() -> None:
    fa = FakeAdapter("instagram", [], {}, like_should_fail=True)
    p = _post("1")
    result: LikeResult = fa.like(p)
    assert result.liked is False
    assert result.reason.startswith("failed:")
    assert fa.likes_attempted == [p]
    assert fa.likes_succeeded == []


def test_session_is_noop_context_manager() -> None:
    fa = FakeAdapter("facebook", [], {})
    with fa.session() as sess:
        assert sess is None
