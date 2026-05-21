"""End-to-end tests for ``run_ig_scan()`` via FakeAdapter — no browser/network.

Slice 3 of OutboundEngagement: scanner is a thin wrapper around
``run_outbound_scan``. Locks the IG contract (10/day default quota,
per-config quota override, today-budget math, ``?`` candidate gate, queue
record shape, approval flag). Fixture in ``conftest.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
) -> Post:
    """Build an IG Post with realistic platform_extra metadata."""
    return Post(
        platform="instagram",
        post_id=post_id,
        post_url=f"https://www.instagram.com/p/{post_id}/",
        text=text,
        author=author,
        source_id=source.id,
        source_name=source.id,
        source_url=source.url,
        platform_extra={
            "category": "training",
            "like_count": like_count,
            "comment_count": 12,
            "weeks_old": 0,
        },
    )


def _hashtag(tag: str) -> FakeSource:
    return FakeSource(
        id=tag, name=tag, url=f"https://www.instagram.com/explore/tags/{tag}/"
    )


def _override_ig_quota(config_path: Path, comments_per_day: int) -> None:
    """Rewrite the fixture's config.json to set IG comments_per_day."""
    cfg = json.loads(config_path.read_text())
    cfg.setdefault("rate_limits", {}).setdefault("instagram", {})[
        "comments_per_day"
    ] = comments_per_day
    config_path.write_text(json.dumps(cfg))


def _high_score_question(idx: int) -> str:
    """Question-form text: food + brand "ollie" + question = 0.80, clears gates."""
    return f"best ollie dog food kibble nutrition recipe number {idx} for my dog?"


def _single_post_adapter(hashtag_id: str, post: Post) -> FakeAdapter:
    """Build a FakeAdapter with one hashtag holding one post."""
    return FakeAdapter("instagram", [_hashtag(hashtag_id)], {hashtag_id: [post]})


# --- tests ------------------------------------------------------------------


def test_ig_scan_cherry_picks_by_quota(
    ig_environment: dict[str, Any],
) -> None:
    """quota=2 override: from 5 candidates, top-2 by score reach the queue.

    Locks cherry-pick math independent of the 10/day default. Scores from
    `score_relevance(text)` only — top tier (=1.10) wins over second (=0.90).
    """
    state_dir: Path = ig_environment["state_dir"]
    _override_ig_quota(ig_environment["config_path"], comments_per_day=2)

    hashtag = _hashtag("doggear")
    texts = [
        # Top tier (1.10)
        "best ollie dog food kibble nutrition recipe with gps tracker?",
        "fi collar canicross running with ollie food kibble diet?",
        # Second tier (0.90) — queueable but dropped at quota.
        "homemade dog food recipe nutrition for running dog?",
        "raw kibble protein diet for canicross dog?",
        "puppy nutrition kibble for trail hike running dog?",
    ]
    posts = [_make_ig_post(f"p{i + 1}", t, hashtag) for i, t in enumerate(texts)]
    adapter = FakeAdapter("instagram", [hashtag], {"doggear": posts})

    report = run_ig_scan(adapter=adapter)
    assert report is not None
    assert report.queued == 2

    queue = json.loads((state_dir / "comment_queue.json").read_text())
    queued_ids = {r["post_id"] for r in queue}
    assert queued_ids == {"p1", "p2"}, (
        f"Cherry-pick should select p1+p2 (top tier), got {queued_ids}"
    )


def test_ig_scan_quota_default_is_10(ig_environment: dict[str, Any]) -> None:
    """Without per-test override, IG default quota is 10/day.

    15 high-score candidates -> exactly 10 reach the queue.
    """
    state_dir: Path = ig_environment["state_dir"]
    hashtag = _hashtag("dogfood")
    posts = [
        _make_ig_post(f"p{i}", _high_score_question(i), hashtag) for i in range(15)
    ]
    adapter = FakeAdapter("instagram", [hashtag], {"dogfood": posts})

    report = run_ig_scan(adapter=adapter)
    assert report is not None
    assert report.queued == 10, f"default quota=10, got {report.queued}"

    queue = json.loads((state_dir / "comment_queue.json").read_text())
    assert len(queue) == 10


def test_ig_scan_existing_today_reduces_budget(
    ig_environment: dict[str, Any],
) -> None:
    """Pre-seeded today-records consume the day's quota budget.

    4 IG records today + quota=10 -> next run gets budget=6. With 8 fresh
    candidates, exactly 6 are queued.
    """
    state_dir: Path = ig_environment["state_dir"]
    queue_path: Path = ig_environment["queue_path"]
    today_iso = datetime.now(UTC).isoformat()
    queue_path.write_text(json.dumps([
        {
            "platform": "instagram",
            "post_id": f"existing_{i}",
            "post_url": f"https://www.instagram.com/p/existing_{i}/",
            "queued_at": today_iso,
            "status": "pending",
        }
        for i in range(4)
    ]))

    hashtag = _hashtag("dogfood")
    posts = [
        _make_ig_post(f"fresh_{i}", _high_score_question(i), hashtag)
        for i in range(8)
    ]
    adapter = FakeAdapter("instagram", [hashtag], {"dogfood": posts})

    report = run_ig_scan(adapter=adapter)
    assert report is not None
    assert report.queued == 6, f"budget=10-4=6, got {report.queued}"

    queue = json.loads((state_dir / "comment_queue.json").read_text())
    fresh_ids = [r["post_id"] for r in queue if r["post_id"].startswith("fresh_")]
    assert len(fresh_ids) == 6
    assert sum(1 for r in queue if r["post_id"].startswith("existing_")) == 4


def test_ig_scan_likes_inline_above_candidate_threshold(
    ig_environment: dict[str, Any],
) -> None:
    """Posts clearing candidate_threshold (0.70) get like() called inline."""
    hashtag = _hashtag("dogfood")
    posts = [
        # food + active + brand "ollie" = 0.90; no "?" so not a comment
        # candidate but still clears the like gate.
        _make_ig_post(
            "p1",
            "ollie dog food kibble nutrition recipe with running gear",
            hashtag,
        ),
        _make_ig_post("p2", "weather forecast today", hashtag),
    ]
    adapter = FakeAdapter("instagram", [hashtag], {"dogfood": posts})

    run_ig_scan(adapter=adapter)

    liked_ids = {p.post_id for p in adapter.likes_succeeded}
    assert "p1" in liked_ids
    assert "p2" not in liked_ids


def test_ig_scan_requires_approval_always_true(
    ig_environment: dict[str, Any],
) -> None:
    """Per CLAUDE.md every queued IG record must have requires_approval=True."""
    state_dir: Path = ig_environment["state_dir"]
    post = _make_ig_post(
        "p1",
        "best ollie dog food kibble nutrition recipe for my dog?",
        _hashtag("dogs"),
    )
    run_ig_scan(adapter=_single_post_adapter("dogs", post))

    queue = json.loads((state_dir / "comment_queue.json").read_text())
    assert len(queue) >= 1
    assert all(rec["requires_approval"] is True for rec in queue)


def test_ig_scan_queue_record_shape(ig_environment: dict[str, Any]) -> None:
    """IG queue record has the expected IG keys with the expected values."""
    state_dir: Path = ig_environment["state_dir"]
    post = _make_ig_post(
        "p1",
        "best ollie dog food kibble nutrition for my dog?",
        _hashtag("dogs"),
        author="@trainer_jane",
        like_count=342,
    )
    run_ig_scan(adapter=_single_post_adapter("dogs", post))

    queue = json.loads((state_dir / "comment_queue.json").read_text())
    assert len(queue) == 1
    rec = queue[0]
    expected_keys = {
        "platform", "post_url", "post_id", "post_text", "hashtag", "author",
        "category", "relevance_score", "like_count", "queued_at", "status",
        "requires_approval", "draft_comment",
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
    ig_environment: dict[str, Any],
) -> None:
    """Posts the adapter pre-filters (e.g. competitor) are never liked."""
    state_dir: Path = ig_environment["state_dir"]
    hashtag = _hashtag("t1")
    text = "best ollie dog food kibble nutrition for my dog?"
    posts = [
        _make_ig_post("p_competitor", text, hashtag),
        _make_ig_post("p_ok", text, hashtag),
    ]
    adapter = FakeAdapter(
        "instagram", [hashtag], {"t1": posts},
        pre_filter_overrides={"p_competitor": "competitor"},
    )

    run_ig_scan(adapter=adapter)

    attempted_ids = {p.post_id for p in adapter.likes_attempted}
    assert "p_competitor" not in attempted_ids
    assert "p_ok" in attempted_ids

    queue = json.loads((state_dir / "comment_queue.json").read_text())
    assert "p_competitor" not in {r["post_id"] for r in queue}


def test_ig_scan_empty_sources_returns_zero(
    ig_environment: dict[str, Any],
) -> None:
    """No hashtags -> no work, report.queued=0, queue stays empty."""
    state_dir: Path = ig_environment["state_dir"]
    adapter = FakeAdapter("instagram", sources=[], posts_by_source={})

    report = run_ig_scan(adapter=adapter)
    assert report is not None
    assert report.queued == 0
    assert json.loads((state_dir / "comment_queue.json").read_text()) == []


def test_ig_scan_skips_low_score_posts(
    ig_environment: dict[str, Any],
) -> None:
    """Posts below candidate_threshold are not liked and not queued."""
    state_dir: Path = ig_environment["state_dir"]
    hashtag = _hashtag("randoms")
    posts = [
        _make_ig_post("low1", "sunset photo from yesterday", hashtag),
        _make_ig_post("low2", "morning coffee thoughts", hashtag),
    ]
    adapter = FakeAdapter("instagram", [hashtag], {"randoms": posts})

    report = run_ig_scan(adapter=adapter)
    assert report is not None
    assert report.queued == 0
    assert adapter.likes_succeeded == []
    assert json.loads((state_dir / "comment_queue.json").read_text()) == []
