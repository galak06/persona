"""Retry + accumulator tests for ``run_outbound_scan``.

Two behaviours that a code review caught, split out of
``test_pipeline_inline_comment.py`` (already at the 300-line cap):

  1. A comment that was ATTEMPTED and FAILED must leave the post
     retryable. The seen-mark used to be written immediately after the
     duplicate gate, so a post whose comment submission missed was retired
     forever without ever having been commented on.
  2. Single-pass mode never cherry-picks, so it must not accumulate the
     candidate list that only the two-stage path reads — while still
     reporting the candidate COUNT, which both modes expose.

Fakes + factories live in ``_pipeline_fakes``.
"""

from __future__ import annotations

from lib.engagement.adapters.fake import FakeAdapter
from lib.engagement.pipeline import _Counters
from lib.engagement.scan_results import PostOutcome
from tests.lib.engagement._pipeline_fakes import (
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
    report, _d, _rt, _dr, _q = run(adapter, dedup=dedup, inline_comment=True)

    assert adapter.comments != [], "fixture assumption: the post was attempted"
    assert report.comments_posted == 0
    assert dedup.seen_marked == [], "a failed comment burned the post"


def test_failed_comment_is_retried_on_the_next_run() -> None:
    """The point of leaving it unmarked: the next scan gets another go."""
    dedup = FakeIterateOnceDedup()
    run(_ig_adapter(comment_should_fail=True), dedup=dedup, inline_comment=True)

    retry = _ig_adapter()
    report, _d, _rt, _dr, _q = run(retry, dedup=dedup, inline_comment=True)

    assert retry.comments == [("p0", "DRAFT for https://x/p/p0")]
    assert report.comments_posted == 1
    assert ("instagram", "p0") in dedup.seen_marked, "the retry must now stick"


def test_terminal_outcomes_are_still_marked() -> None:
    """Only a FAILED comment is retryable; a posted one is terminal."""
    dedup = FakeIterateOnceDedup()
    run(_ig_adapter(), dedup=dedup, inline_comment=True)

    assert ("instagram", "p0") in dedup.seen_marked


# --- 2. the candidate list is not built when nothing reads it ----------------


def test_inline_mode_counts_candidates_without_retaining_them() -> None:
    """Single-pass mode never cherry-picks, so retaining candidates is waste.

    The COUNT still has to be reported, so only the (post, score) pairs are
    dropped — the accumulator is the unit that draws that distinction.
    """
    post = make_post("p0", "food question?")
    scored = PostOutcome(candidate_score=0.85)

    inline = _Counters("instagram", collect_candidates=False)
    inline.add(post, scored)
    assert inline.candidate_count == 1, "the report still needs the count"
    assert inline.candidates == [], "inline mode accumulated a dead list"

    two_stage = _Counters("facebook", collect_candidates=True)
    two_stage.add(post, scored)
    assert two_stage.candidates == [(post, 0.85)], "cherry-pick lost its input"


def test_inline_scan_reports_candidates_but_queues_nothing() -> None:
    """End-to-end: the count survives even though the list is never built."""
    report, _d, _rt, _dr, queue = run(
        FakeAdapter("instagram", [make_src("s1")], {"s1": make_ig_posts(5)}),
        dedup=FakeIterateOnceDedup(),
        inline_comment=True,
    )

    assert report.candidates == 5
    assert report.queued == 0
    assert queue.appended == []
