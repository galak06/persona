"""End-to-end tests for ``run_ig_scan()`` via FakeAdapter — no browser/network.

Post-PR#36 Instagram is SINGLE-PASS: ``run_ig_scan`` wires the shared pipeline
with ``inline_comment=True``, so each qualifying post is scored, liked, and
commented in the ONE visit that opened it — no Redis queue, no
``scripts/ig_comment.py`` handoff. These tests assert that contract through the
``run_ig_scan`` ENTRY POINT (``config.json`` -> ``EngagementPolicy``, the IG
``_score_post`` meta signal, real ``rate_limiter`` gating, ``ScanDedup`` wiring,
last-run stamp) — wiring the pipeline fakes can't see. The exhaustive single-
pass UNIT coverage lives in ``test_pipeline_inline_comment`` /
``test_pipeline_dry_run`` / ``test_pipeline_retry``.

Retired two-stage cases (IG no longer cherry-picks or queues) and where their
behavior moved:
  - ``cherry_picks_by_quota`` / ``quota_default_is_10`` / ``queue_record_shape``
    -> ``test_ig_scan_likes_and_comments_inline_single_pass`` (here) +
    ``test_pipeline_inline_comment::test_inline_comment_posts_through_the_adapter``
    / ``..._reports_zero_queued``.
  - ``existing_today_reduces_budget`` -> ``test_ig_scan_comment_quota_caps_
    inline_comments`` (unit: ``..::test_comment_quota_caps_the_run``); the budget
    is now a ``rate_limiter`` count, not a queue tally.
  - ``requires_approval_always_true`` -> the 0.75-0.80 band is simply not auto-
    commented: ``..::test_below_auto_approve_threshold_likes_but_does_not_comment``.

Fixture in ``conftest.py`` / ``_env_builders.py``.
"""

from __future__ import annotations

import json
from datetime import date
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


def _high_score_question(idx: int) -> str:
    """Question-form text scoring 1.0 (food + brand "ollie" + '?'): auto-comments."""
    return f"best ollie dog food kibble nutrition recipe number {idx} for my dog?"


def _single_post_adapter(hashtag_id: str, post: Post) -> FakeAdapter:
    """Build a FakeAdapter with one hashtag holding one post."""
    return FakeAdapter("instagram", [_hashtag(hashtag_id)], {hashtag_id: [post]})


# --- 1. the single-pass headline: like AND comment in one visit -------------


def test_ig_scan_likes_and_comments_inline_single_pass(
    ig_environment: dict[str, Any],
) -> None:
    """A qualifying question-post is liked AND commented in one visit; nothing queued.

    Replaces the retired two-stage queue tests (cherry-pick / queue-record-shape
    / quota-default). The like+comment-in-one-visit contract itself is unit-
    tested in ``test_pipeline_inline_comment::test_inline_comment_posts_through_
    the_adapter``; here it runs through the real ``run_ig_scan`` entry point.
    """
    post = _make_ig_post(
        "p1",
        "best ollie dog food kibble nutrition recipe for my dog?",
        _hashtag("dogs"),
    )
    adapter = _single_post_adapter("dogs", post)

    report = run_ig_scan(adapter=adapter)
    assert report is not None
    # One visit, both actions.
    assert [p.post_id for p in adapter.likes_succeeded] == ["p1"]
    assert [post_id for post_id, _text in adapter.comments] == ["p1"]
    assert report.likes_succeeded == 1
    assert report.comments_posted == 1
    assert report.comments_declined == 0
    # Single-pass never queues.
    assert report.queued == 0


# --- 2. like gate + the IG '?' comment-candidacy gate ------------------------


def test_ig_scan_likes_candidate_but_skips_comment_without_question(
    ig_environment: dict[str, Any],
) -> None:
    """Posts clearing candidate_threshold (0.70) are liked inline; the IG '?'
    gate still means a high-score NON-question post is liked but never commented.

    (Old two-stage name: ``test_ig_scan_likes_inline_above_candidate_threshold``.)
    """
    hashtag = _hashtag("dogfood")
    posts = [
        # 1.10 — high enough to comment, but no '?' so the IG candidacy gate
        # blocks the comment while the like still fires.
        _make_ig_post(
            "p1",
            "ollie dog food kibble nutrition recipe with running gear",
            hashtag,
        ),
        _make_ig_post("p2", "weather forecast today", hashtag),
    ]
    adapter = FakeAdapter("instagram", [hashtag], {"dogfood": posts})

    report = run_ig_scan(adapter=adapter)
    liked = {p.post_id for p in adapter.likes_succeeded}
    assert liked == {"p1"}
    assert "p2" not in {p.post_id for p in adapter.likes_attempted}
    # p1 was liked but is NOT a comment candidate (no question mark).
    assert adapter.comments == []
    assert report.comments_posted == 0


# --- 3. pre-filter rejections never engage -----------------------------------


def test_ig_scan_respects_pre_filter_rejections(
    ig_environment: dict[str, Any],
) -> None:
    """Posts the adapter pre-filters (e.g. competitor) are never liked or commented."""
    hashtag = _hashtag("t1")
    text = "best ollie dog food kibble nutrition recipe for my dog?"
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
    commented_ids = {post_id for post_id, _text in adapter.comments}
    assert "p_competitor" not in commented_ids
    assert "p_ok" in commented_ids


# --- 4. low-score posts are skipped entirely ---------------------------------


def test_ig_scan_skips_low_score_posts(
    ig_environment: dict[str, Any],
) -> None:
    """Posts below candidate_threshold are neither liked nor commented; queued=0."""
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
    assert adapter.comments == []
    assert report.comments_posted == 0


# --- 5. empty sources = zeroed report ----------------------------------------


def test_ig_scan_empty_sources_returns_zero(
    ig_environment: dict[str, Any],
) -> None:
    """No hashtags -> no work: zeroed report, nothing liked or commented."""
    adapter = FakeAdapter("instagram", sources=[], posts_by_source={})

    report = run_ig_scan(adapter=adapter)
    assert report is not None
    assert report.sources_visited == 0
    assert report.posts_scanned == 0
    assert report.likes_attempted == 0
    assert report.comments_posted == 0
    assert report.queued == 0
    assert adapter.comments == []


# --- 6. the comment budget caps inline comments ------------------------------


def test_ig_scan_comment_quota_caps_inline_comments(
    ig_environment: dict[str, Any],
) -> None:
    """The IG comment budget (``rate_limiter``, ``data/rate_limits.json`` = 10/day)
    caps how many comments a single-pass run posts — likes are unaffected.

    Single-pass replacement for the retired queue "today reduces budget" /
    "cherry-pick by quota" math: the budget is now the ``rate_limiter`` daily
    count, not a queue-record tally. Pre-spend 8/10 -> only 2 of 5 question-
    posts get commented, while all 5 are still liked. (Unit analog:
    ``test_pipeline_inline_comment::test_comment_quota_caps_the_run``.)
    """
    rate_path: Path = ig_environment["rate_path"]
    today = date.today().isoformat()
    rate_path.write_text(json.dumps({today: {"instagram:comment": 8}}))

    hashtag = _hashtag("dogfood")
    posts = [
        _make_ig_post(f"p{i}", _high_score_question(i), hashtag) for i in range(5)
    ]
    adapter = FakeAdapter("instagram", [hashtag], {"dogfood": posts})

    report = run_ig_scan(adapter=adapter)
    assert report is not None
    assert report.comments_posted == 2, (
        f"budget = 10-8 = 2, got {report.comments_posted}"
    )
    assert len(adapter.comments) == 2
    # The like gate is independent of the comment budget: all 5 were liked.
    assert report.likes_succeeded == 5
    assert report.queued == 0


# --- 7. last-run stamp -------------------------------------------------------


def test_ig_scan_updates_last_run_on_success(
    ig_environment: dict[str, Any],
) -> None:
    """A successful run stamps last_run.json with the single-pass counters."""
    last_run_path: Path = ig_environment["last_run_path"]
    post = _make_ig_post(
        "p1",
        "best ollie dog food kibble nutrition recipe for my dog?",
        _hashtag("dogs"),
    )
    run_ig_scan(adapter=_single_post_adapter("dogs", post))

    last_run = json.loads(last_run_path.read_text())
    assert "ig_scanner" in last_run
    ig = last_run["ig_scanner"]
    assert ig["status"] == "success"
    assert ig["hashtags_scanned"] == 1
    assert ig["posts_liked"] == 1
    assert ig["posts_commented"] == 1


# --- 8. dry run posts nothing and burns no state -----------------------------


def test_ig_scan_dry_run_posts_nothing(
    ig_environment: dict[str, Any],
) -> None:
    """``dry_run=True``: nothing liked or commented, no last-run stamp, but the
    report still shows would-be work.

    The ``--dry-run`` bug (a "dry" scan performing REAL likes) is unit-locked in
    ``test_pipeline_dry_run``; this exercises the same guard through the
    ``run_ig_scan`` wrapper (skip-guards bypassed, no last-run stamp written).
    """
    last_run_path: Path = ig_environment["last_run_path"]
    post = _make_ig_post(
        "p1",
        "best ollie dog food kibble nutrition recipe for my dog?",
        _hashtag("dogs"),
    )
    adapter = _single_post_adapter("dogs", post)

    report = run_ig_scan(adapter=adapter, dry_run=True)
    assert report is not None
    # Nothing left the process.
    assert adapter.likes_attempted == []
    assert adapter.likes_succeeded == []
    assert adapter.comments == []
    # ...but the report shows what a live run WOULD have done.
    assert report.likes_attempted == 1
    assert report.comments_attempted == 1
    assert report.comments_posted == 0
    # A dry run consumes no state: the already-ran-today stamp is not burned.
    assert json.loads(last_run_path.read_text()) == {}
