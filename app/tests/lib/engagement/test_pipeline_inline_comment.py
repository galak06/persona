"""Single-pass (inline comment) behavior tests for ``run_outbound_scan``.

Engagement used to be a two-stage flow: the scan liked a post and pushed it
to a queue, and a separate commenter run re-opened it later to comment.
``inline_comment=True`` collapses that into ONE visit — open, score, like,
draft, comment — with no queue at all. Both engagers
(``scripts/ig_engager.py`` and ``scripts/fb_engager.py``) run this path.

These tests lock that contract, including the parts that are easy to break
silently: the auto-approve gate is read from ``EngagementPolicy`` (not
hardcoded), an agent decline never posts, a dry run still drafts but never
posts, and every OPENED post is marked so it is never opened again.

Sibling files own the rest of the single-pass contract (300-line cap):
``test_pipeline_record_publish`` — persistence side effects of a posted
comment (``record_publish`` / ``log_engagement``) + the injected
``CommentGate``; ``test_pipeline_like_only`` — ``inline_comment=False``
(now only a like-only degrade) and comment-incapable adapters;
``test_pipeline_retry`` — the iterate-once seen-mark contract.

Fakes + factories live in ``_pipeline_fakes``.
"""

from __future__ import annotations

from lib.engagement.adapters.fake import FakeAdapter
from tests.lib.engagement._pipeline_fakes import (
    FakeDrafter,
    FakeIterateOnceDedup,
    FakeLog,
    FakeRateTracker,
    make_ig_posts,
    make_policy,
    make_src,
    run,
)


def _ig_adapter(n: int = 1) -> FakeAdapter:
    """IG adapter with ``n`` high-score (0.85), question-form posts."""
    return FakeAdapter("instagram", [make_src("s1")], {"s1": make_ig_posts(n)})


def _events(log: FakeLog) -> list[str]:
    """Event names (first token) of every log line, in order."""
    return [msg.split(" ", 1)[0] for _level, msg in log.calls]


def _line(log: FakeLog, event: str) -> str:
    """The first log line whose event name is ``event``."""
    return next(msg for _level, msg in log.calls if msg.startswith(event))


# --- 1. the happy path: like AND comment in one visit ------------------------


def test_inline_comment_posts_through_the_adapter() -> None:
    """A qualifying post is commented during the same visit that liked it."""
    adapter = _ig_adapter(1)
    dedup = FakeIterateOnceDedup()
    report, _d, _rt, drafter = run(adapter, dedup=dedup, inline_comment=True)

    assert adapter.comments == [("p0", "DRAFT for https://x/p/p0")]
    assert drafter.calls != [], "drafter must run inside the scan"
    assert report.comments_attempted == 1
    assert report.comments_posted == 1
    assert report.comments_declined == 0
    # Liked too — one visit, both actions.
    assert [p.post_id for p in adapter.likes_succeeded] == ["p0"]


def test_inline_comment_records_rate_action_and_dedup_mark() -> None:
    """A posted comment spends the daily comment budget and is marked engaged."""
    adapter = _ig_adapter(1)
    dedup = FakeIterateOnceDedup()
    _r, _d, rt, _dr = run(adapter, dedup=dedup, inline_comment=True)

    assert ("instagram", "comment") in rt.recorded
    assert ("instagram", "p0", "comment", "src") in dedup.engaged
    # ...and the human-cadence delay is honoured after posting.
    assert ("instagram", "comment") in rt.delays


def test_inline_comment_failure_is_not_counted_as_posted() -> None:
    """A failed submission spends no budget and is logged as a failure."""
    adapter = FakeAdapter(
        "instagram", [make_src("s1")], {"s1": make_ig_posts(1)},
        comment_should_fail=True,
    )
    log = FakeLog()
    report, dedup, rt, _dr = run(
        adapter, dedup=FakeIterateOnceDedup(), log=log, inline_comment=True
    )

    assert adapter.comments != [], "the adapter was still called"
    assert report.comments_posted == 0
    assert report.comments_attempted == 1
    assert ("instagram", "comment") not in rt.recorded
    assert [e for e in dedup.engaged if e[2] == "comment"] == []
    assert "post_comment_failed" in _events(log)


# --- 2. the agent decline is the approval gate -------------------------------


def test_agent_decline_posts_nothing() -> None:
    """``engage: false`` (empty draft) must never reach the adapter."""
    adapter = _ig_adapter(1)
    report, _d, rt, drafter = run(
        adapter,
        dedup=FakeIterateOnceDedup(),
        drafter=FakeDrafter(engage=False),
        inline_comment=True,
    )

    assert drafter.calls != [], "the drafter must still be consulted"
    assert adapter.comments == [], "declined post was commented on"
    assert report.comments_declined == 1
    assert report.comments_posted == 0
    assert ("instagram", "comment") not in rt.recorded


def test_agent_decline_is_logged_with_a_reason() -> None:
    """A decline must be attributable, not a silent drop."""
    log = FakeLog()
    run(
        _ig_adapter(1),
        dedup=FakeIterateOnceDedup(),
        drafter=FakeDrafter(engage=False),
        log=log,
        inline_comment=True,
    )

    assert "comment_declined" in _events(log)
    assert "reason=" in _line(log, "comment_declined")


def test_declined_post_is_still_marked_seen() -> None:
    """We paid the visit: a declined post must never be opened again."""
    dedup = FakeIterateOnceDedup()
    run(
        _ig_adapter(1),
        dedup=dedup,
        drafter=FakeDrafter(engage=False),
        inline_comment=True,
    )

    assert ("instagram", "p0") in dedup.seen_marked


# --- 3. dry run: draft the preview, send nothing -----------------------------


def test_dry_run_drafts_but_never_comments() -> None:
    """The preview must show what would be said without saying it."""
    adapter = _ig_adapter(2)
    log = FakeLog()
    report, _d, _rt, drafter = run(
        adapter,
        dedup=FakeIterateOnceDedup(),
        log=log,
        inline_comment=True,
        dry_run=True,
    )

    assert drafter.calls != [], "dry run must still draft (that's the preview)"
    assert adapter.comments == [], "dry run posted a real comment"
    assert report.comments_attempted == 2, "report shows would-be comments"
    assert report.comments_posted == 0
    assert "post_comment_dry_run" in _events(log)
    assert "post_commented" not in _events(log)


def test_dry_run_consumes_no_state() -> None:
    """No rate spend, no engagement mark, and no seen-mark on a dry run."""
    dedup = FakeIterateOnceDedup()
    _r, _d, rt, _dr = run(
        _ig_adapter(2), dedup=dedup, inline_comment=True, dry_run=True
    )

    assert ("instagram", "comment") not in rt.recorded
    assert dedup.engaged == []
    assert dedup.seen_marked == [], "a dry run must leave posts eligible"


# --- 4. quota + threshold gates ----------------------------------------------


def test_exhausted_comment_quota_attempts_nothing() -> None:
    """With no comment budget left, the drafter is never even called."""
    adapter = _ig_adapter(3)
    report, _d, _rt, drafter = run(
        adapter,
        dedup=FakeIterateOnceDedup(),
        rate_tracker=FakeRateTracker(comments_left=0),
        inline_comment=True,
    )

    assert drafter.calls == [], "no LLM spend once the quota is gone"
    assert adapter.comments == []
    assert report.comments_posted == 0


def test_comment_quota_caps_the_run() -> None:
    """Only as many comments as the tracker allows are posted."""
    adapter = _ig_adapter(5)
    report, _d, _rt, _dr = run(
        adapter,
        dedup=FakeIterateOnceDedup(),
        rate_tracker=FakeRateTracker(comments_left=2),
        inline_comment=True,
    )

    assert len(adapter.comments) == 2
    assert report.comments_posted == 2


def test_below_auto_approve_threshold_likes_but_does_not_comment() -> None:
    """The 0.75-0.80 borderline band has no human in this loop, so it's skipped.

    The threshold is read from ``EngagementPolicy.approval_threshold``; this
    post clears the comment gate (0.75) but not the approval gate (0.80).
    """
    adapter = _ig_adapter(1)
    dedup = FakeIterateOnceDedup()
    log = FakeLog()
    policy = make_policy()
    assert policy.approval_threshold == 0.80, "fixture assumption"

    report, _d, _rt, drafter = run(
        adapter,
        policy=policy,
        dedup=dedup,
        log=log,
        score=lambda post: 0.78,
        inline_comment=True,
    )

    assert drafter.calls == []
    assert adapter.comments == []
    assert report.comments_posted == 0
    assert "comment_skipped_needs_approval" in _events(log)
    # ...but it was still liked and still marked seen.
    assert [p.post_id for p in adapter.likes_succeeded] == ["p0"]
    assert ("instagram", "p0") in dedup.seen_marked


def test_non_question_post_is_not_commented() -> None:
    """The IG '?' candidacy gate still applies in single-pass mode."""
    posts = make_ig_posts(1, has_question=False)
    adapter = FakeAdapter("instagram", [make_src("s1")], {"s1": posts})
    report, _d, _rt, drafter = run(
        adapter, dedup=FakeIterateOnceDedup(), inline_comment=True
    )

    assert drafter.calls == []
    assert adapter.comments == []
    assert report.comments_posted == 0
