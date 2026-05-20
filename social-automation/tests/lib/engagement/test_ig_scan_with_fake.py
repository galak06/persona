"""End-to-end test of ``run_ig_scan()`` driven by FakeAdapter — no browser,
no network, no Google/Telegram API.

Slice 2 of OutboundEngagement: locks in the cherry-pick-top-N + inline-like
behavior that ig_scan still owns. Slice 3 will move cherry-pick into a
shared pipeline; these tests stay the gate.

Shared fixture (``ig_environment``) lives in ``conftest.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.ig_scan import run_ig_scan

from lib.engagement.adapters.fake import FakeAdapter, FakeSource
from lib.engagement.post import Post

# --- helpers ----------------------------------------------------------------


def _make_ig_post(
    post_id: str,
    text: str,
    source: FakeSource,
    *,
    author: str = "@dogtrainer",
    like_count: int = 300,
    comment_count: int = 12,
    weeks_old: int = 0,
) -> Post:
    """Build an IG Post with realistic platform_extra metadata."""
    return Post(
        platform="instagram",
        post_id=post_id,
        post_url=f"https://www.instagram.com/p/{post_id}/",
        text=text,
        author=author,
        source_id=source.id,
        source_name=source.id,  # hashtag string
        source_url=source.url,
        platform_extra={
            "category": "training",
            "like_count": like_count,
            "comment_count": comment_count,
            "weeks_old": weeks_old,
        },
    )


def _hashtag(tag: str) -> FakeSource:
    return FakeSource(
        id=tag,
        name=tag,
        url=f"https://www.instagram.com/explore/tags/{tag}/",
    )


# --- tests ------------------------------------------------------------------


def test_ig_scan_cherry_picks_top_2_from_five_candidates(
    ig_environment: dict[str, Path],
) -> None:
    """Even with 5 comment-candidates, only top-2 by score reach the queue."""
    state_dir = ig_environment["state_dir"]
    hashtag = _hashtag("doggear")
    # All texts include "?" so they hit the IG comment-candidate gate; scores
    # diverge via brand/category signals so the cherry-pick is deterministic.
    posts = [
        # Top: food + brand "ollie" + question + meta bonus -> ~1.00
        _make_ig_post("p1", "best ollie dog food kibble nutrition recipe?", hashtag),
        # Second: gps + brand "fi" + question + meta -> ~0.90
        _make_ig_post(
            "p2", "fi collar gps tracker running canicross with dog?", hashtag
        ),
        # Mid: food + question + meta -> ~0.80
        _make_ig_post("p3", "homemade chicken recipe for picky eater dog?", hashtag),
        _make_ig_post(
            "p4", "raw kibble protein diet for sensitive stomach dog?", hashtag
        ),
        _make_ig_post("p5", "best dog food brand for puppy nutrition?", hashtag),
    ]
    adapter = FakeAdapter("instagram", [hashtag], {"doggear": posts})

    queued_count = run_ig_scan(adapter=adapter)

    queue = json.loads((state_dir / "comment_queue.json").read_text())
    queued_ids = [r["post_id"] for r in queue]
    assert queued_count == 2
    assert len(queued_ids) == 2
    assert set(queued_ids) == {"p1", "p2"}, (
        f"Cherry-pick should select p1+p2 (highest scores), got {queued_ids}"
    )


def test_ig_scan_likes_inline_above_candidate_threshold(
    ig_environment: dict[str, Path],
) -> None:
    """Posts clearing candidate_threshold (0.70) get like() called inline."""
    hashtag = _hashtag("dogfood")
    posts = [
        # food (+0.40) + brand "ollie" (+0.20) + meta (+0.20) = 0.80 -> liked
        _make_ig_post("p1", "ollie dog food kibble nutrition recipe", hashtag),
        # Well below threshold -> not liked
        _make_ig_post("p2", "weather forecast today", hashtag),
    ]
    adapter = FakeAdapter("instagram", [hashtag], {"dogfood": posts})

    run_ig_scan(adapter=adapter)

    liked_ids = {p.post_id for p in adapter.likes_succeeded}
    assert "p1" in liked_ids
    assert "p2" not in liked_ids


def test_ig_scan_requires_approval_always_true(
    ig_environment: dict[str, Path],
) -> None:
    """Per CLAUDE.md every queued IG record must have requires_approval=True."""
    state_dir = ig_environment["state_dir"]
    hashtag = _hashtag("dogs")
    adapter = FakeAdapter(
        "instagram",
        [hashtag],
        {
            "dogs": [
                _make_ig_post(
                    "p1",
                    "best ollie dog food kibble nutrition recipe for my dog?",
                    hashtag,
                )
            ]
        },
    )

    run_ig_scan(adapter=adapter)

    queue = json.loads((state_dir / "comment_queue.json").read_text())
    assert len(queue) >= 1
    for rec in queue:
        assert rec["requires_approval"] is True


def test_ig_scan_queue_record_shape(ig_environment: dict[str, Path]) -> None:
    """IG queue record has the exact 13 IG keys with the expected values."""
    state_dir = ig_environment["state_dir"]
    hashtag = _hashtag("dogs")
    adapter = FakeAdapter(
        "instagram",
        [hashtag],
        {
            "dogs": [
                _make_ig_post(
                    "p1",
                    "best ollie dog food kibble nutrition for my dog?",
                    hashtag,
                    author="@trainer_jane",
                    like_count=342,
                )
            ]
        },
    )

    run_ig_scan(adapter=adapter)

    queue = json.loads((state_dir / "comment_queue.json").read_text())
    assert len(queue) == 1
    rec = queue[0]
    expected_keys = {
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
    assert set(rec.keys()) == expected_keys
    assert rec["platform"] == "instagram"
    assert rec["hashtag"] == "dogs"
    assert rec["author"] == "@trainer_jane"
    assert rec["like_count"] == 342
    assert rec["status"] == "pending"
    assert rec["draft_comment"].startswith("DRAFT-")
    assert rec["post_id"] == "p1"


def test_ig_scan_respects_pre_filter_rejections(
    ig_environment: dict[str, Path],
) -> None:
    """Posts the adapter pre-filters (e.g. competitor) are never liked."""
    state_dir = ig_environment["state_dir"]
    hashtag = _hashtag("t1")
    posts = [
        _make_ig_post(
            "p_competitor",
            "best ollie dog food kibble nutrition for my dog?",
            hashtag,
        ),
        _make_ig_post(
            "p_ok", "best ollie dog food kibble nutrition for my dog?", hashtag
        ),
    ]
    adapter = FakeAdapter(
        "instagram",
        [hashtag],
        {"t1": posts},
        pre_filter_overrides={"p_competitor": "competitor"},
    )

    run_ig_scan(adapter=adapter)

    attempted_ids = {p.post_id for p in adapter.likes_attempted}
    assert "p_competitor" not in attempted_ids
    assert "p_ok" in attempted_ids

    queue = json.loads((state_dir / "comment_queue.json").read_text())
    queued_ids = {r["post_id"] for r in queue}
    assert "p_competitor" not in queued_ids


def test_ig_scan_empty_sources_returns_zero(
    ig_environment: dict[str, Path],
) -> None:
    """No hashtags -> no work, returns 0, no queue writes."""
    state_dir = ig_environment["state_dir"]
    adapter = FakeAdapter("instagram", sources=[], posts_by_source={})

    queued = run_ig_scan(adapter=adapter)

    assert queued == 0
    queue = json.loads((state_dir / "comment_queue.json").read_text())
    assert queue == []


def test_ig_scan_skips_low_score_posts(
    ig_environment: dict[str, Path],
) -> None:
    """Posts below candidate_threshold are not liked and not queued."""
    state_dir = ig_environment["state_dir"]
    hashtag = _hashtag("randoms")
    adapter = FakeAdapter(
        "instagram",
        [hashtag],
        {
            "randoms": [
                _make_ig_post("low1", "sunset photo from yesterday", hashtag),
                _make_ig_post("low2", "morning coffee thoughts", hashtag),
            ]
        },
    )

    queued = run_ig_scan(adapter=adapter)

    assert queued == 0
    assert adapter.likes_succeeded == []
    queue = json.loads((state_dir / "comment_queue.json").read_text())
    assert queue == []
