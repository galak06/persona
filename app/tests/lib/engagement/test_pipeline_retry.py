"""Iterate-once (seen-mark) + accumulator tests for ``run_outbound_scan``.

The single-pass seen-mark contract, split out of
``test_pipeline_inline_comment.py`` (300-line cap):

  1. A comment that was ATTEMPTED and FAILED must leave the post
     retryable. The seen-mark used to be written immediately after the
     duplicate gate, so a post whose comment submission missed was retired
     forever without ever having been commented on.
  2. Every other outcome is terminal: commented, low-score and
     pre-filtered posts are ALL marked seen, the mark sticks across runs,
     and a dedup collaborator without the optional ``mark_seen``
     capability degrades to a no-op instead of crashing.
  3. The queue stage is retired, so the accumulator only COUNTS comment
     candidates — the (post, score) list the old cherry-pick read no
     longer exists, and ``ScanReport.queued`` is hardwired to 0.

Fakes + factories live in ``_pipeline_fakes``.
"""

from __future__ import annotations

from lib.engagement.adapters.fake import FakeAdapter
from lib.engagement.pipeline import _Counters
from lib.engagement.scan_results import PostOutcome
from tests.lib.engagement._pipeline_fakes import (
    FakeDedup,
    FakeIterateOnceDedup,
    make_ig_posts,
    make_post,
    make_src,
    run,
)


def _ig_adapter(*, comment_should_fail: bool = False) -> FakeAdapter:
    """One high-score, question-form IG post; optionally un-commentable."""
    return FakeAdapter(
        "instagram",
        [make_src("s1")],
        {"s1": make_ig_posts(1)},
        comment_should_fail=comment_should_fail,
    )


# --- 1. a failed comment stays retryable -------------------------------------


def test_failed_comment_leaves_the_post_unmarked() -> None:
    """A comment we tried and failed to post must stay retryable.

    ``lib/ig/comment_post.py`` returns False whenever its selector chain
    misses — a routine outcome, not a decision. Marking such a post seen
    would retire it forever without ever having commented on it.
    """
    adapter = _ig_adapter(comment_should_fail=True)
    dedup = FakeIterateOnceDedup()
    report, _d, _rt, _dr = run(adapter, dedup=dedup, inline_comment=True)

    assert adapter.comments != [], "fixture assumption: the post was attempted"
    assert report.comments_posted == 0
    assert dedup.seen_marked == [], "a failed comment burned the post"


def test_failed_comment_is_retried_on_the_next_run() -> None:
    """The point of leaving it unmarked: the next scan gets another go."""
    dedup = FakeIterateOnceDedup()
    run(_ig_adapter(comment_should_fail=True), dedup=dedup, inline_comment=True)

    retry = _ig_adapter()
    report, _d, _rt, _dr = run(retry, dedup=dedup, inline_comment=True)

    assert retry.comments == [("p0", "DRAFT for https://x/p/p0")]
    assert report.comments_posted == 1
    assert ("instagram", "p0") in dedup.seen_marked, "the retry must now stick"


def test_terminal_outcomes_are_still_marked() -> None:
    """Only a FAILED comment is retryable; a posted one is terminal."""
    dedup = FakeIterateOnceDedup()
    run(_ig_adapter(), dedup=dedup, inline_comment=True)

    assert ("instagram", "p0") in dedup.seen_marked


# --- 2. iterate-once: every other outcome is terminal ------------------------


def test_every_opened_post_is_marked_seen_whatever_the_outcome() -> None:
    """Commented, low-score and pre-filtered posts all get marked."""
    src = make_src("s1")
    posts = [
        make_post("p_ok", "food question?"),        # commented
        make_post("p_low", "boring question?"),     # below candidate threshold
        make_post("p_pre", "food question?"),       # pre-filtered
    ]
    adapter = FakeAdapter(
        "instagram", [src], {"s1": posts},
        pre_filter_overrides={"p_pre": "competitor"},
    )
    dedup = FakeIterateOnceDedup()
    run(adapter, dedup=dedup, inline_comment=True)

    marked = {post_id for _platform, post_id in dedup.seen_marked}
    assert marked == {"p_ok", "p_low", "p_pre"}


def test_marked_posts_are_skipped_on_the_next_run() -> None:
    """The seen-mark must land in the store ``is_duplicate`` reads."""
    dedup = FakeIterateOnceDedup()
    run(_ig_adapter(), dedup=dedup, inline_comment=True)

    second = _ig_adapter()
    report, _d, _rt, drafter = run(second, dedup=dedup, inline_comment=True)

    assert report.posts_scanned == 1, "the post was still enumerated"
    assert second.likes_attempted == [], "an opened post was re-opened"
    assert second.comments == []
    assert drafter.calls == []


def test_dedup_without_mark_seen_capability_is_a_no_op() -> None:
    """A collaborator lacking `mark_seen` (bare `deduplication`) still works."""
    adapter = _ig_adapter()
    report, _d, _rt, _dr = run(adapter, dedup=FakeDedup(), inline_comment=True)

    assert report.comments_posted == 1, "scan must not depend on mark_seen"


# --- 3. the accumulator counts candidates without a queue --------------------


def test_counters_count_candidates_and_hardwire_queued_to_zero() -> None:
    """Nothing cherry-picks anymore, so the accumulator keeps only the COUNT.

    The report shape (``ScanReport``) still carries ``queued`` for its
    consumers' benefit; the accumulator freezes it at 0.
    """
    post = make_post("p0", "food question?")
    scored = PostOutcome(candidate_score=0.85)

    counters = _Counters("instagram")
    counters.add(post, scored)
    assert counters.candidate_count == 1, "the report still needs the count"

    report = counters.to_report()
    assert report.candidates == 1
    assert report.queued == 0, "the queue stage is retired"


def test_inline_scan_reports_candidates_but_queues_nothing() -> None:
    """End-to-end: the count survives even though no queue exists."""
    report, _d, _rt, _dr = run(
        FakeAdapter("instagram", [make_src("s1")], {"s1": make_ig_posts(5)}),
        dedup=FakeIterateOnceDedup(),
        inline_comment=True,
    )

    assert report.candidates == 5
    assert report.queued == 0
