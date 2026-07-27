"""Behavior-preservation contract: Post.to_queue_record produces the legacy
queue-record shape for each platform.

This is THE gate for the OutboundEngagement refactor. fb_scan.py + ig_scan.py
write queue records consumed by comment_poster downstream; the exact key set
per platform is load-bearing. If anyone changes to_queue_record in a way that
breaks the legacy shape, these tests fail.
"""

from __future__ import annotations

from typing import Any

from lib.engagement.post import Post

EXPECTED_FB_KEYS: frozenset[str] = frozenset(
    {
        "platform",
        "post_url",
        "post_id",
        "post_text",
        "group_name",
        "group_url",
        "category",
        "relevance_score",
        "queued_at",
        "status",
        "requires_approval",
        "draft_comment",
    }
)

EXPECTED_IG_KEYS: frozenset[str] = frozenset(
    {
        "platform",
        "post_url",
        "post_id",
        "post_text",
        "hashtag",
        "author",
        "category",
        "relevance_score",
        "like_count",
        "queued_at",
        "status",
        "requires_approval",
        "draft_comment",
    }
)


def test_facebook_record_shape_exact_keys() -> None:
    post = Post(
        platform="facebook",
        post_id="987654",
        post_url="https://facebook.com/groups/123456/posts/987654",
        text="What food do you feed your shepherd?",
        source_id="123456",
        source_name="Dog Group",
        source_url="https://facebook.com/groups/123456",
        platform_extra={"category": "food"},
    )
    rec: dict[str, Any] = post.to_queue_record(
        score=0.825,
        draft="hello!",
        requires_approval=False,
        queued_at="2026-05-20T10:00:00+00:00",
    )
    assert set(rec.keys()) == EXPECTED_FB_KEYS
    assert rec["platform"] == "facebook"
    assert rec["post_id"] == "987654"
    assert rec["post_url"] == "https://facebook.com/groups/123456/posts/987654"
    assert rec["post_text"] == "What food do you feed your shepherd?"
    assert rec["group_name"] == "Dog Group"
    assert rec["group_url"] == "https://facebook.com/groups/123456"
    assert rec["category"] == "food"
    assert rec["relevance_score"] == 0.825
    assert rec["queued_at"] == "2026-05-20T10:00:00+00:00"
    assert rec["status"] == "pending"
    assert rec["requires_approval"] is False
    assert rec["draft_comment"] == "hello!"


def test_instagram_record_shape_exact_keys() -> None:
    post = Post(
        platform="instagram",
        post_id="DABCxyz",
        post_url="https://instagram.com/p/DABCxyz",
        text="Just got Nalla a new harness!",
        author="some_user",
        source_id="dogtraining",
        source_name="dogtraining",
        source_url="https://instagram.com/explore/tags/dogtraining",
        platform_extra={"like_count": 342, "category": "training"},
    )
    rec: dict[str, Any] = post.to_queue_record(
        score=0.79,
        draft="hi!",
        requires_approval=True,
        queued_at="2026-05-20T19:00:00+00:00",
    )
    assert set(rec.keys()) == EXPECTED_IG_KEYS
    assert rec["platform"] == "instagram"
    assert rec["post_id"] == "DABCxyz"
    assert rec["post_url"] == "https://instagram.com/p/DABCxyz"
    assert rec["post_text"] == "Just got Nalla a new harness!"
    assert rec["hashtag"] == "dogtraining"
    assert rec["author"] == "some_user"
    assert rec["category"] == "training"
    assert rec["relevance_score"] == 0.79
    assert rec["like_count"] == 342
    assert rec["queued_at"] == "2026-05-20T19:00:00+00:00"
    assert rec["status"] == "pending"
    assert rec["requires_approval"] is True
    assert rec["draft_comment"] == "hi!"


def test_relevance_score_rounded_to_three_decimals() -> None:
    post = Post(
        platform="facebook",
        post_id="p1",
        post_url="https://x/p/1",
        text="t",
        source_name="g",
        source_url="https://x/g",
    )
    rec = post.to_queue_record(
        score=0.8254321,
        draft="d",
        requires_approval=False,
        queued_at="2026-05-20T10:00:00+00:00",
    )
    assert rec["relevance_score"] == 0.825
