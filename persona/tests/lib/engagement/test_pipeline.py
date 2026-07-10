"""Behavior tests for ``run_outbound_scan``.

Exercises the pipeline via ``FakeAdapter`` and small in-test fakes.
Fakes + factories live in ``_pipeline_fakes`` to keep this file lean.
No I/O, no ``tmp_path``, no production singletons.
"""

from __future__ import annotations

from lib.engagement.adapters.fake import FakeAdapter
from tests.lib.engagement._pipeline_fakes import (
    FakeDedup,
    FakeQueueIO,
    FakeRateTracker,
    make_ig_posts,
    make_policy,
    make_post,
    make_src,
    run,
)


def test_empty_source_list_returns_empty_report() -> None:
    adapter = FakeAdapter("instagram", [], {})
    report, _d, _rt, _dr, q = run(adapter)
    assert report.sources_visited == 0
    assert report.posts_scanned == 0
    assert report.queued == 0
    assert q.appended == []
    assert q.saved is True


def test_high_score_post_queued_top_n_by_score() -> None:
    """IG quota=3: top-3 of 5 same-score posts queued; all valid."""
    src = make_src("s1")
    posts = make_ig_posts(5)
    adapter = FakeAdapter("instagram", [src], {"s1": posts})
    report, _d, _rt, _dr, q = run(adapter, policy=make_policy(ig_comment_quota=3))
    assert report.candidates == 5
    assert report.queued == 3
    assert len(q.appended) == 3
    for record in q.appended:
        assert record["relevance_score"] == 0.85


def test_dedup_skips_already_seen_posts() -> None:
    src = make_src("s1")
    posts = [
        make_post("p1", "food question?"),
        make_post("p2", "food question?"),
        make_post("p3", "food question?"),
    ]
    adapter = FakeAdapter("instagram", [src], {"s1": posts})
    report, _d, _rt, _dr, q = run(adapter, dedup=FakeDedup(seen={"p1"}))
    assert report.posts_scanned == 3
    queued_ids = [r["post_id"] for r in q.appended]
    assert "p1" not in queued_ids
    assert set(queued_ids) == {"p2", "p3"}


def test_pre_filter_rejection_counted_in_report() -> None:
    src = make_src("s1")
    posts = [make_post("p1", "food question?"), make_post("p2", "food question?")]
    adapter = FakeAdapter(
        "instagram", [src], {"s1": posts},
        pre_filter_overrides={"p1": "competitor"},
    )
    report, _d, _rt, _dr, q = run(adapter)
    assert report.pre_filtered == {"competitor": 1}
    queued_ids = {r["post_id"] for r in q.appended}
    assert "p1" not in queued_ids and "p2" in queued_ids
    # Pre-filtered post is never offered to .like()
    assert all(post.post_id != "p1" for post in adapter.likes_attempted)


def test_below_candidate_threshold_skipped() -> None:
    """Posts without 'food' score 0.40 (below 0.70) — no like, no queue."""
    src = make_src("s1")
    posts = [make_post("p1", "boring question?"), make_post("p2", "ok?")]
    adapter = FakeAdapter("instagram", [src], {"s1": posts})
    report, _d, _rt, _dr, q = run(adapter)
    assert report.candidates == 0
    assert report.queued == 0
    assert q.appended == []
    assert adapter.likes_attempted == []


def test_ig_like_step_called_for_qualifying_posts() -> None:
    src = make_src("s1")
    adapter = FakeAdapter("instagram", [src], {"s1": [make_post("p1", "food question?")]})
    report, _d, rt, _dr, _q = run(adapter)
    assert len(adapter.likes_succeeded) == 1
    assert adapter.likes_succeeded[0].post_id == "p1"
    assert ("instagram", "like") in rt.recorded
    assert report.likes_succeeded == 1
    assert report.likes_attempted == 1


def test_fb_like_failure_still_queues_post() -> None:
    """A like that fails (LikeResult.failed) does not block queueing.

    Requires ``fb_like_quota>0`` — the pipeline gates the like step by
    ``policy.daily_like_quota[platform] > 0`` to avoid calling
    ``rate_tracker.can_act("facebook", "like")`` on the production
    rate_limiter (which would raise ``ValueError: Unknown action key``).
    Slice 4 wires real FB inline-like; here we just exercise the path.
    """
    src = make_src("g1", name="grp1")
    posts = [
        make_post(
            "p1", "food question?",
            platform="facebook", source_id="g1", source_name="grp1",
        )
    ]
    adapter = FakeAdapter("facebook", [src], {"g1": posts}, like_should_fail=True)
    rt = FakeRateTracker(visits_left=10, likes_left=10)
    report, _d, _rt, _dr, q = run(
        adapter,
        policy=make_policy(fb_like_quota=10),
        rate_tracker=rt,
    )
    assert report.likes_attempted == 1
    assert report.likes_succeeded == 0
    assert len(q.appended) == 1
    assert q.appended[0]["post_id"] == "p1"


def test_ig_requires_approval_always_true() -> None:
    """IG always sets requires_approval=True, even at high score."""
    src = make_src("s1")
    # Score 0.85 ≥ approval_threshold 0.80, but IG forces approval.
    adapter = FakeAdapter("instagram", [src], {"s1": [make_post("p1", "food question?")]})
    _r, _d, _rt, _dr, q = run(adapter)
    assert len(q.appended) == 1
    assert q.appended[0]["requires_approval"] is True


def test_quota_cap_enforced() -> None:
    src = make_src("s1")
    adapter = FakeAdapter("instagram", [src], {"s1": make_ig_posts(10)})
    report, _d, _rt, _dr, q = run(adapter, policy=make_policy(ig_comment_quota=5))
    assert report.candidates == 10
    assert report.queued == 5
    assert len(q.appended) == 5


def test_existing_today_reduces_budget() -> None:
    src = make_src("s1")
    adapter = FakeAdapter("instagram", [src], {"s1": make_ig_posts(10)})
    queue = FakeQueueIO(existing_today_count=3)
    report, _d, _rt, _dr, q = run(
        adapter, policy=make_policy(ig_comment_quota=5), queue_io=queue
    )
    assert report.candidates == 10
    assert report.queued == 2
    assert len(q.appended) == 2


def test_ig_question_mark_gate() -> None:
    """High-score IG post lacking '?' is liked but NOT queued."""
    src = make_src("s1")
    adapter = FakeAdapter(
        "instagram", [src], {"s1": [make_post("p1", "food story without question.")]}
    )
    report, _d, _rt, _dr, q = run(adapter)
    assert report.likes_succeeded == 1
    assert report.candidates == 0
    assert q.appended == []


def test_fb_visit_budget_aborts_iteration() -> None:
    """visits_left=2 → only 2 of 5 FB sources visited."""
    sources = [make_src(f"g{i}", name=f"grp{i}") for i in range(5)]
    posts_by_source = {
        s.id: [
            make_post(
                f"{s.id}_p", "food question?",
                platform="facebook", source_id=s.id, source_name=s.name,
            )
        ]
        for s in sources
    }
    adapter = FakeAdapter("facebook", sources, posts_by_source)
    rt = FakeRateTracker(visits_left=2, likes_left=0)
    report, _d, _rt, _dr, _q = run(adapter, rate_tracker=rt)
    assert report.sources_visited == 2
    assert report.posts_scanned == 2  # one post per visited source


def test_scan_report_counts_accurate() -> None:
    """Multi-source IG run: mix of accepted/rejected/dedup-skipped."""
    s1 = make_src("s1", name="src1")
    s2 = make_src("s2", name="src2")
    s1_posts = [
        make_post("p1", "food question?"),    # queued + liked
        make_post("p2", "boring question?"),  # below threshold
        make_post("p3", "food question?"),    # pre-filtered
        make_post("p4", "food question?"),    # dedup skip
    ]
    s2_posts = [
        make_post("p5", "food question?"),    # queued + liked
        make_post("p6", "food story."),       # liked, no '?' → not queued
    ]
    adapter = FakeAdapter(
        "instagram", [s1, s2],
        {"s1": s1_posts, "s2": s2_posts},
        pre_filter_overrides={"p3": "competitor"},
    )
    report, _d, _rt, _dr, q = run(adapter, dedup=FakeDedup(seen={"p4"}))

    assert report.sources_visited == 2
    assert report.posts_scanned == 6
    # candidates: p1, p5 (p2 below thresh; p3 pre-filtered; p4 dedup; p6 no '?')
    assert report.candidates == 2
    # likes: p1, p5, p6 qualify (score ≥ candidate threshold);
    #   p2 below, p3 pre-filtered, p4 dedup-skipped before like
    assert report.likes_attempted == 3
    assert report.likes_succeeded == 3
    assert report.queued == 2
    assert report.pre_filtered == {"competitor": 1}
    assert {r["post_id"] for r in q.appended} == {"p1", "p5"}
