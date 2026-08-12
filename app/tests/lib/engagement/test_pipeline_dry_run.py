"""Dry-run behavior tests for ``run_outbound_scan``.

``--dry-run`` used to be silently swallowed by ``scripts/ig_engager.py``, so a
"dry" scan performed REAL likes on a live Instagram account. These tests lock
the contract: with ``dry_run=True`` nothing leaves the process — no
``adapter.like()``, no dedup mark, no rate-limit spend — while the report
still reports what a live run *would* have done. (The inline-comment side of
the dry-run contract — draft the preview, post nothing — lives in
``test_pipeline_inline_comment``.)

Split out of ``test_pipeline.py`` to keep that file under the 300-line cap.
Fakes + factories live in ``_pipeline_fakes``.
"""

from __future__ import annotations

from lib.engagement.adapters.fake import FakeAdapter
from tests.lib.engagement._pipeline_fakes import (
    FakeLog,
    make_ig_posts,
    make_post,
    make_src,
    run,
)


def _ig_adapter(n: int = 3) -> FakeAdapter:
    """IG adapter with ``n`` high-score, question-form posts."""
    return FakeAdapter("instagram", [make_src("s1")], {"s1": make_ig_posts(n)})


# --- 1. no like lands -------------------------------------------------------


def test_dry_run_never_calls_adapter_like() -> None:
    """The bug this file exists for: dry run must not touch the live account."""
    adapter = _ig_adapter()
    run(adapter, dry_run=True)
    assert adapter.likes_attempted == [], "adapter.like() called during a dry run"
    assert adapter.likes_succeeded == []


def test_dry_run_report_still_shows_would_be_likes() -> None:
    """Attempts are counted so the report shows what a live run would do."""
    report, _d, _rt, _dr = run(_ig_adapter(3), dry_run=True)
    assert report.likes_attempted == 3, "dry run should report would-be likes"
    assert report.likes_succeeded == 0, "nothing actually liked in a dry run"


def test_dry_run_spends_no_like_rate_limit_and_marks_no_dedup() -> None:
    """No like recorded against the daily cap, no post marked engaged."""
    report, dedup, rt, _dr = run(_ig_adapter(3), dry_run=True)
    assert ("instagram", "like") not in rt.recorded
    assert dedup.engaged == []
    assert report.likes_attempted == 3


def test_dry_run_logs_are_labelled_as_dry_run() -> None:
    """Output must never be mistakable for a live run."""
    log = FakeLog()
    run(_ig_adapter(1), log=log, dry_run=True)
    events = [msg.split(" ", 1)[0] for _level, msg in log.calls]
    assert "post_like_dry_run" in events
    # A skipped like is not a *failed* like — don't cry wolf.
    assert "post_liked" not in events
    assert "post_like_failed" not in events


# --- 2. candidates are still reported ---------------------------------------


def test_dry_run_still_counts_candidates() -> None:
    """The report tells the operator what a live run would consider."""
    report, _d, _rt, _dr = run(_ig_adapter(10), dry_run=True)
    assert report.candidates == 10
    assert report.queued == 0, "the queue stage is retired; queued is always 0"


def test_dry_run_does_not_call_the_drafter_in_like_only_mode() -> None:
    """Like-only mode (``inline_comment=False``) never drafts, dry or live.

    (Single-pass mode DOES draft under a dry run — that's the preview —
    see ``test_pipeline_inline_comment::test_dry_run_drafts_but_never_comments``.)
    """
    _r, _d, _rt, drafter = run(_ig_adapter(3), dry_run=True)
    assert drafter.calls == []


# --- 3. regression: live behavior unchanged ---------------------------------


def test_live_run_still_likes() -> None:
    """dry_run=False (the default) behaves exactly as before."""
    adapter = _ig_adapter(3)
    report, dedup, rt, _dr = run(adapter, dry_run=False)

    assert len(adapter.likes_attempted) == 3
    assert len(adapter.likes_succeeded) == 3
    assert report.likes_attempted == 3
    assert report.likes_succeeded == 3
    assert ("instagram", "like") in rt.recorded
    assert [e[2] for e in dedup.engaged] == ["like"] * 3


def test_dry_run_and_live_run_agree_on_counts() -> None:
    """Same input: the dry run predicts exactly what the live run does.

    This is the property that makes a dry run worth trusting.
    """
    dry_report, _d, _rt, _dr = run(_ig_adapter(10), dry_run=True)
    live_adapter = _ig_adapter(10)
    live_report, _d2, _rt2, _dr2 = run(live_adapter)

    assert dry_report.candidates == live_report.candidates
    assert dry_report.likes_attempted == live_report.likes_attempted
    # ...but only the live run produced side effects.
    assert len(live_adapter.likes_succeeded) == 10


def test_dry_run_default_is_false() -> None:
    """Omitting the flag must keep the live path — no accidental no-op scans."""
    adapter = _ig_adapter(1)
    run(adapter)
    assert len(adapter.likes_attempted) == 1


def test_dry_run_still_honours_pre_filter_and_score_gates() -> None:
    """Dry run reports the same rejections a live run would make."""
    src = make_src("s1")
    posts = [
        make_post("p1", "food question?"),     # candidate
        make_post("p2", "boring question?"),   # below threshold
        make_post("p3", "food question?"),     # pre-filtered
    ]
    adapter = FakeAdapter(
        "instagram", [src], {"s1": posts},
        pre_filter_overrides={"p3": "competitor"},
    )
    report, _d, _rt, _dr = run(adapter, dry_run=True)

    assert report.posts_scanned == 3
    assert report.pre_filtered == {"competitor": 1}
    assert report.candidates == 1
    assert adapter.likes_attempted == []
