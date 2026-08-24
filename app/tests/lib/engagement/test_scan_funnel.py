"""Run-funnel instrumentation for ``run_outbound_scan``.

Three things are pinned here, all of them diagnostics:

1. every scored post emits a ``post_scored`` line carrying score AND caption
   length -- the pair that separates "the scrape broke" (score 0.15,
   caption_len 0) from "this post is irrelevant" (score 0.15, caption_len 400)
2. ``ScanReport`` names every funnel exit, including ``pre_filtered``, which
   was computed and thrown away with zero consumers
3. the run summary carries the funnel, so "where did the posts go?" is
   answerable without log archaeology

And the fourth, which is the constraint on the other three: NONE of it changes
what a scan does. ``test_instrumentation_leaves_scan_outcomes_identical``
snapshots the full engagement result of a mixed scan against values captured
from the pre-instrumentation pipeline.
"""

from __future__ import annotations

from lib.engagement.adapters.fake import FakeAdapter
from lib.engagement.extraction import (
    EXTRACTION_EMPTY_CAPTION,
    EXTRACTION_FAILED,
    EXTRACTION_OK,
    EXTRACTION_STATUS_KEY,
)
from lib.engagement.run_summary import build_summary, format_funnel, funnel_counts, log_funnel
from tests.lib.engagement._pipeline_fakes import (
    FakeDedup,
    FakeLog,
    make_policy,
    make_post,
    make_src,
    run,
)


def _lines(log: FakeLog, event: str) -> list[str]:
    """Rendered log lines whose event name (first word) is `event`."""
    return [text for _level, text in log.lines if text.split(" ", 1)[0] == event]


# --- 1. per-post score logging ----------------------------------------------


def test_every_scored_post_logs_its_score_and_caption_length() -> None:
    """Not just candidates: the whole distribution, or it proves nothing."""
    src = make_src("s1")
    posts = [
        make_post("high", "food question?"),  # 0.85 -- candidate
        make_post("low", "unrelated chatter here"),  # 0.40 -- below threshold
        make_post("empty", ""),  # 0.40 -- the scrape-failure shape
    ]
    log = FakeLog()
    run(FakeAdapter("instagram", [src], {"s1": posts}), log=log)

    scored = _lines(log, "post_scored")
    assert len(scored) == 3, "a score must be logged for every scored post"
    assert "post_id=high score=0.85 caption_len=14" in scored[0]
    assert "post_id=low score=0.40 caption_len=22" in scored[1]
    assert "post_id=empty score=0.40 caption_len=0" in scored[2]


def test_caption_length_separates_a_failed_scrape_from_irrelevant_content() -> None:
    """The two posts score identically; only caption_len tells them apart."""
    src = make_src("s1")
    scraped_nothing = make_post("broken", "")
    genuinely_dull = make_post("dull", "x" * 400)
    log = FakeLog()
    run(FakeAdapter("instagram", [src], {"s1": [scraped_nothing, genuinely_dull]}), log=log)

    scored = _lines(log, "post_scored")
    assert "score=0.40" in scored[0] and "caption_len=0" in scored[0]
    assert "score=0.40" in scored[1] and "caption_len=400" in scored[1]


def test_score_is_logged_below_the_near_miss_floor() -> None:
    """`post_skipped` starts at 0.5; the gap under it was the blind spot."""
    src = make_src("s1")
    log = FakeLog()
    run(FakeAdapter("instagram", [src], {"s1": [make_post("p1", "")]}), log=log)

    assert _lines(log, "post_skipped") == [], "0.40 is still under the near-miss floor"
    assert len(_lines(log, "post_scored")) == 1, "but the score is recorded anyway"


# --- 2. report funnel counters ----------------------------------------------


def test_report_counts_extraction_failures_separately_from_empty_captions() -> None:
    src = make_src("s1")
    posts = [
        make_post("f1", "", platform_extra={EXTRACTION_STATUS_KEY: EXTRACTION_FAILED}),
        make_post("f2", "", platform_extra={EXTRACTION_STATUS_KEY: EXTRACTION_FAILED}),
        make_post("e1", "", platform_extra={EXTRACTION_STATUS_KEY: EXTRACTION_EMPTY_CAPTION}),
        make_post("ok", "food?", platform_extra={EXTRACTION_STATUS_KEY: EXTRACTION_OK}),
    ]
    report, _d, _rt, _dr = run(FakeAdapter("instagram", [src], {"s1": posts}))

    assert report.extraction_failed == 2
    assert report.empty_caption == 1
    assert report.posts_scanned == 4


def test_adapters_that_report_no_extraction_status_count_as_healthy() -> None:
    """Absence of the key means "this adapter does not report", not "failed"."""
    src = make_src("s1")
    report, _d, _rt, _dr = run(FakeAdapter("instagram", [src], {"s1": [make_post("p1", "food?")]}))
    assert report.extraction_failed == 0
    assert report.empty_caption == 0


def test_extraction_failure_is_counted_even_when_the_post_is_a_duplicate() -> None:
    """Scrape health describes the POST, not the gate it left through.

    A run whose extraction broke must say so even if every post it opened was
    already seen -- otherwise the counter goes quiet exactly when the scan is
    scanning nothing new.
    """
    src = make_src("s1")
    posts = [make_post("p1", "", platform_extra={EXTRACTION_STATUS_KEY: EXTRACTION_FAILED})]
    report, _d, _rt, _dr = run(
        FakeAdapter("instagram", [src], {"s1": posts}), dedup=FakeDedup(seen={"p1"})
    )
    assert report.duplicates == 1
    assert report.extraction_failed == 1


def test_report_names_every_funnel_exit() -> None:
    src = make_src("s1")
    posts = [
        make_post("dup", "food question?"),
        make_post("filtered", "food question?"),
        make_post("low", "boring"),
        make_post("cand", "food question?"),
    ]
    adapter = FakeAdapter(
        "instagram",
        [src],
        {"s1": posts},
        pre_filter_overrides={"filtered": "competitor"},
    )
    report, _d, _rt, _dr = run(adapter, dedup=FakeDedup(seen={"dup"}), inline_comment=True)

    assert funnel_counts(report) == {
        "scanned": 4,
        "duplicates": 1,
        "pre_filtered": 1,
        "extract_fail": 0,
        "empty_caption": 0,
        "below_threshold": 1,
        "candidates": 1,
        "drafted": 1,
        "posted": 1,
        "declined": 0,
    }


def test_pre_filtered_is_populated_and_surfaced() -> None:
    """`pre_filtered` had zero consumers; the funnel is its first one."""
    src = make_src("s1")
    posts = [make_post("a", "food?"), make_post("b", "food?"), make_post("c", "food?")]
    adapter = FakeAdapter(
        "instagram",
        [src],
        {"s1": posts},
        pre_filter_overrides={"a": "competitor", "b": "too_old"},
    )
    report, _d, _rt, _dr = run(adapter)

    assert report.pre_filtered == {"competitor": 1, "too_old": 1}
    assert funnel_counts(report)["pre_filtered"] == 2, "the summary totals the reasons"


# --- 3. run summary ---------------------------------------------------------


def test_summary_carries_the_funnel_after_the_historical_head() -> None:
    src = make_src("s1")
    report, _d, _rt, _dr = run(
        FakeAdapter("instagram", [src], {"s1": make_ig_pair()}), inline_comment=True
    )
    summary = build_summary(report, source_label="Hashtags", comment_quota=10, dry_run=False)

    assert summary.startswith(
        "Hashtags: 1 | Liked: 2 | Commented: 2/10 | Agent declined: 0 | Funnel: "
    )
    assert "scanned=2" in summary
    assert "extract_fail=0" in summary


def test_dry_run_summary_keeps_its_own_head_and_gains_the_funnel() -> None:
    src = make_src("s1")
    report, _d, _rt, _dr = run(
        FakeAdapter("instagram", [src], {"s1": make_ig_pair()}),
        inline_comment=True,
        dry_run=True,
    )
    summary = build_summary(report, source_label="Groups", comment_quota=5, dry_run=True)

    assert summary.startswith("DRY RUN (nothing liked, commented or recorded) | Groups: 1 | ")
    assert "Would like: 2" in summary
    assert f"Funnel: {format_funnel(report)}" in summary


def test_log_funnel_emits_one_structured_line() -> None:
    src = make_src("s1")
    report, _d, _rt, _dr = run(FakeAdapter("instagram", [src], {"s1": make_ig_pair()}))
    log = FakeLog()
    log_funnel(report, log)

    assert len(log.lines) == 1
    level, line = log.lines[0]
    assert level == "info"
    assert line.startswith("scan_funnel platform=instagram sources=1 ")
    assert "scanned=2" in line and "candidates=2" in line


# --- 4. behaviour is unchanged ----------------------------------------------


def make_ig_pair() -> list:
    """Two high-scoring IG posts with a question mark (comment candidates)."""
    return [make_post("p1", "food question?"), make_post("p2", "food question?")]


def test_instrumentation_leaves_scan_outcomes_identical() -> None:
    """Snapshot of a mixed scan, captured from the pre-instrumentation pipeline.

    Every value below was produced by ``run_outbound_scan`` BEFORE any of this
    slice's logging, counters or extraction statuses existed, and reproduced by
    running this test against that revision. If instrumentation ever starts
    steering the scan -- a status treated as a filter, a counter consulted by a
    gate -- one of these changes.
    """
    src_a, src_b = make_src("s1"), make_src("s2")
    posts = {
        "s1": [
            make_post("dup", "food question?"),
            make_post("filtered", "food question?"),
            make_post("cand", "food question?"),
            # Same shape a failed scrape produces: empty text, no question.
            make_post(
                "scraped_empty", "", platform_extra={EXTRACTION_STATUS_KEY: EXTRACTION_FAILED}
            ),
        ],
        "s2": [
            make_post("liked_only", "food statement."),  # 0.85, no '?' -> like, no comment
            make_post("low", "unrelated"),  # 0.40 -> nothing
        ],
    }
    adapter = FakeAdapter(
        "instagram",
        [src_a, src_b],
        posts,
        pre_filter_overrides={"filtered": "competitor"},
    )
    report, dedup, _rt, drafter = run(
        adapter,
        dedup=FakeDedup(seen={"dup"}),
        policy=make_policy(),
        inline_comment=True,
    )

    assert [p.post_id for p in adapter.likes_attempted] == ["cand", "liked_only"]
    assert adapter.comments == [("cand", "DRAFT for https://x/p/cand")]
    assert [c["post_url"] for c in drafter.calls] == ["https://x/p/cand"]
    assert (report.sources_visited, report.posts_scanned) == (2, 6)
    assert (report.candidates, report.likes_succeeded) == (1, 2)
    assert (report.comments_attempted, report.comments_posted) == (1, 1)
    assert report.comments_declined == 0
    assert report.pre_filtered == {"competitor": 1}
    assert report.pre_filtered_posts == [("filtered", "competitor")]
    assert dedup.claims == [("instagram", "cand")]


def test_extraction_status_does_not_steer_scoring_or_engagement() -> None:
    """The same post, with and without a FAILED marker, is treated identically."""
    src = make_src("s1")
    marked = FakeAdapter(
        "instagram",
        [src],
        {
            "s1": [
                make_post(
                    "p1",
                    "food question?",
                    platform_extra={EXTRACTION_STATUS_KEY: EXTRACTION_FAILED},
                )
            ]
        },
    )
    plain = FakeAdapter("instagram", [src], {"s1": [make_post("p1", "food question?")]})

    marked_report, _d1, _rt1, _dr1 = run(marked, inline_comment=True)
    plain_report, _d2, _rt2, _dr2 = run(plain, inline_comment=True)

    assert marked.comments == plain.comments
    assert [p.post_id for p in marked.likes_attempted] == [p.post_id for p in plain.likes_attempted]
    assert marked_report.candidates == plain_report.candidates == 1
    # The ONLY difference between the two runs is the diagnostic counter.
    assert (marked_report.extraction_failed, plain_report.extraction_failed) == (1, 0)
