"""Unit tests for FacebookGroupAdapter's comments-disabled skip (Wave 0).

Covers the pure (non-Playwright) seams added by the "comments disabled"
feature:
  - ``_build_post`` carrying the ``comments_disabled`` flag from the raw
    extraction dict into ``Post.platform_extra``.
  - ``pre_filter`` returning ``"comments_disabled"`` for flagged posts.
  - Pipeline-level surfacing of that reason via ``ScanReport``.

``FacebookGroupAdapter.__init__`` only reads config dict keys (no browser /
session), so it constructs cheaply with an empty mapping. ``pre_filter`` and
``_build_post`` never touch Playwright runtime state.
"""

from __future__ import annotations

# ruff: noqa: S101, SLF001
#   S101  — pytest tests use `assert` by design (project convention).
#   SLF001 — `_build_post`/`_FBSource` are the only non-Playwright seams for
#            the comments_disabled flag-carry logic, so the unit tests exercise
#            them directly (intentional private-member access).
from lib.engagement.adapters.facebook import FacebookGroupAdapter, _FBSource
from lib.engagement.adapters.fake import FakeAdapter
from lib.engagement.post import Post
from tests.lib.engagement._pipeline_fakes import make_post, make_src, run


def _adapter() -> FacebookGroupAdapter:
    """A minimally-constructed adapter (empty config — no session needed)."""
    return FacebookGroupAdapter({})


def _src() -> _FBSource:
    return _FBSource(id="g1", name="grp1", url="https://x/groups/g1")


def _fb_post(*, comments_disabled: bool) -> Post:
    return Post(
        platform="facebook",
        post_id="p1",
        post_url="https://x/p/p1",
        text="food question?",
        source_id="g1",
        source_name="grp1",
        source_url="https://x/groups/g1",
        platform_extra={"comments_disabled": comments_disabled},
    )


# --- (a) pre_filter ---------------------------------------------------------


def test_pre_filter_returns_reason_when_comments_disabled() -> None:
    adapter = _adapter()
    assert adapter.pre_filter(_fb_post(comments_disabled=True)) == "comments_disabled"


def test_pre_filter_returns_none_when_flag_false() -> None:
    adapter = _adapter()
    assert adapter.pre_filter(_fb_post(comments_disabled=False)) is None


def test_pre_filter_returns_none_when_key_absent() -> None:
    adapter = _adapter()
    post = Post(
        platform="facebook",
        post_id="p1",
        post_url="https://x/p/p1",
        text="food question?",
        source_id="g1",
        source_name="grp1",
        source_url="https://x/groups/g1",
        platform_extra={"comment_count": 4, "category": "food"},
    )
    assert adapter.pre_filter(post) is None


# --- (b) _build_post carries the flag ---------------------------------------


def test_build_post_carries_comments_disabled_true() -> None:
    adapter = _adapter()
    raw = {
        "text": "homemade food question?",
        "url": "https://x/p/abc123",
        "comment_count": 4,
        "comments_disabled": True,
    }
    post = adapter._build_post(raw, _src(), "food")
    assert post is not None
    assert post.platform_extra["comments_disabled"] is True


def test_build_post_defaults_comments_disabled_false_when_absent() -> None:
    adapter = _adapter()
    raw = {
        "text": "homemade food question?",
        "url": "https://x/p/abc123",
        "comment_count": 4,
    }
    post = adapter._build_post(raw, _src(), "food")
    assert post is not None
    assert post.platform_extra["comments_disabled"] is False


def test_build_post_comments_disabled_false_when_explicitly_false() -> None:
    adapter = _adapter()
    raw = {
        "text": "homemade food question?",
        "url": "https://x/p/abc123",
        "comment_count": 4,
        "comments_disabled": False,
    }
    post = adapter._build_post(raw, _src(), "food")
    assert post is not None
    assert post.platform_extra["comments_disabled"] is False


# --- (c) pipeline surfaces the reason ---------------------------------------


def test_pipeline_surfaces_comments_disabled_in_report() -> None:
    """A post pre-filtered as comments_disabled lands in both report fields
    and is never queued."""
    src = make_src("g1", name="grp1")
    posts = [
        make_post(
            "p1", "food question?",
            platform="facebook", source_id="g1", source_name="grp1",
        ),
        make_post(
            "p2", "food question?",
            platform="facebook", source_id="g1", source_name="grp1",
        ),
    ]
    adapter = FakeAdapter(
        "facebook",
        [src],  # type: ignore[list-item]  # FakeSource is a frozen Source impl
        {"g1": posts},
        pre_filter_overrides={"p1": "comments_disabled"},
    )
    report, _d, _rt, _dr, q = run(adapter)

    # Aggregated count.
    assert report.pre_filtered == {"comments_disabled": 1}
    # Per-post (post_id, reason) tuple.
    assert ("p1", "comments_disabled") in report.pre_filtered_posts
    # The flagged post is not queued; the clean one is.
    queued_ids = {r["post_id"] for r in q.appended}
    assert "p1" not in queued_ids
    assert "p2" in queued_ids
    # Pre-filtered post is never offered to .like().
    assert all(post.post_id != "p1" for post in adapter.likes_attempted)
