"""Persistence + gate tests for the inline (single-pass) comment step.

Two contracts split out of ``test_pipeline_inline_comment.py`` (300-line cap):

  1. Every posted AND every failed comment submission is persisted to
     ``lib.engagements_db`` (``record_publish``) and — when posted — to the
     JSONL engagement log with the canonical ``comment`` action and full
     attribution fields. These records used to exist only on the retired
     two-stage queue path (GAP-2/GAP-3 of the fb-engager cutover).
  2. An injected ``CommentGate`` (e.g. the FB first-comment approval gate)
     vetoes the COMMENT only: the like still lands, the drafter is never
     consulted, and the post is marked seen (terminal, not retryable).

Fakes + factories live in ``_pipeline_fakes``. The conftest's autouse
``_hermetic_engagement_sinks`` fixture stubs both persistence sinks for
every test here; tests that assert on them re-patch with a recorder.
"""

from __future__ import annotations

from typing import Any

import pytest

import lib.engagement.inline_comment as inline_comment
from lib.engagement.adapters.fake import FakeAdapter
from tests.lib.engagement._pipeline_fakes import (
    FakeCommentGate,
    FakeIterateOnceDedup,
    FakeLog,
    make_ig_posts,
    make_src,
    run,
)


def _ig_adapter(n: int = 1, **kwargs: Any) -> FakeAdapter:
    """IG adapter with ``n`` high-score (0.85), question-form posts."""
    return FakeAdapter("instagram", [make_src("s1")], {"s1": make_ig_posts(n)}, **kwargs)


def _capture_record_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Recorder for ``record_publish`` (overrides the conftest autouse stub)."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        inline_comment.engagements_db,
        "record_publish",
        lambda **kwargs: calls.append(kwargs),
    )
    return calls


# --- 1. record_publish: posted and failed both leave a row -------------------


def test_posted_comment_is_recorded_in_engagements_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A posted inline comment writes a `posted` row keyed on the post id."""
    calls = _capture_record_publish(monkeypatch)
    run(_ig_adapter(1), dedup=FakeIterateOnceDedup(), inline_comment=True)

    assert len(calls) == 1
    row = calls[0]
    assert row["platform"] == "instagram"
    assert row["kind"] == "comment"
    assert row["status"] == "posted"
    assert row["ref"] == "p0", "ref must be the third-party post id (dedup key)"
    assert row["target_url"] == "https://x/p/p0"
    assert row["content"] == "DRAFT for https://x/p/p0"
    assert row["posted_at"], "a posted row must carry its timestamp"


def test_failed_comment_submission_is_recorded_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drafted comment the adapter could not post still leaves a row."""
    calls = _capture_record_publish(monkeypatch)
    run(
        _ig_adapter(1, comment_should_fail=True),
        dedup=FakeIterateOnceDedup(),
        inline_comment=True,
    )

    assert len(calls) == 1
    row = calls[0]
    assert row["kind"] == "comment"
    assert row["status"] == "failed"
    assert row["ref"] == "p0"
    # CommentResult.failed prefixes its outcome tag: "failed:<reason>".
    assert row["error"] == "failed:fake_failure"
    assert "posted_at" not in row, "a failed submission has no posted-at time"


def test_dry_run_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dry run posts nothing, so it must persist nothing."""
    calls = _capture_record_publish(monkeypatch)
    run(
        _ig_adapter(2),
        dedup=FakeIterateOnceDedup(),
        inline_comment=True,
        dry_run=True,
    )

    assert calls == []


# --- 2. log_engagement: canonical action + full attribution ------------------


def test_posted_comment_logs_action_comment_with_full_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The JSONL record uses the canonical `comment` action (NOT `commented`)
    and carries the attribution fields the history/reporting readers key on."""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(
        inline_comment,
        "log_engagement",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    run(_ig_adapter(1), dedup=FakeIterateOnceDedup(), inline_comment=True)

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "comment", "canonical action name"
    assert args[1] == "instagram"
    assert args[2] == "src", "target = the source (hashtag/group) name"
    assert args[3] == "DRAFT for https://x/p/p0"
    assert kwargs["post_url"] == "https://x/p/p0"
    assert kwargs["post_id"] == "p0"
    assert kwargs["relevance_score"] == 0.85
    assert "food" in kwargs["post_text"], "original post text is attached"


def test_failed_comment_is_not_logged_as_an_engagement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a POSTED comment is a JSONL engagement; failures go to record_publish."""
    calls: list[object] = []
    monkeypatch.setattr(
        inline_comment,
        "log_engagement",
        lambda *args, **kwargs: calls.append(args),
    )
    run(
        _ig_adapter(1, comment_should_fail=True),
        dedup=FakeIterateOnceDedup(),
        inline_comment=True,
    )

    assert calls == []


# --- 3. the injected CommentGate vetoes the comment only ---------------------


def test_gate_veto_skips_comment_but_keeps_the_like() -> None:
    """A gated post is liked, never drafted, and marked seen (terminal)."""
    adapter = _ig_adapter(1)
    dedup = FakeIterateOnceDedup()
    log = FakeLog()
    gate = FakeCommentGate("blocked_by_test")
    report, _d, rt, drafter = run(
        adapter,
        dedup=dedup,
        log=log,
        inline_comment=True,
        comment_gate=gate,
    )

    assert gate.checked == [("p0", "https://x/s1")], "the gate was consulted"
    assert drafter.calls == [], "a vetoed post must spend no LLM call"
    assert adapter.comments == []
    assert report.comments_attempted == 0
    assert report.comments_declined == 0, "a gate skip is not an agent decline"
    # The like is never gated, and the visit is terminal.
    assert [p.post_id for p in adapter.likes_succeeded] == ["p0"]
    assert ("instagram", "p0") in dedup.seen_marked
    assert ("instagram", "comment") not in rt.recorded
    # ...and the skip is attributable (FakeLog keeps the format string only;
    # the reason= placeholder proves the reason is part of the log line).
    line = next(m for _lvl, m in log.calls if m.startswith("comment_skipped_gated"))
    assert "reason=" in line


def test_gate_allow_lets_the_comment_proceed() -> None:
    """`check() -> None` means allow: the comment posts as if no gate existed."""
    adapter = _ig_adapter(1)
    gate = FakeCommentGate(None)
    report, _d, _rt, drafter = run(
        adapter,
        dedup=FakeIterateOnceDedup(),
        inline_comment=True,
        comment_gate=gate,
    )

    assert gate.checked != []
    assert drafter.calls != []
    assert adapter.comments == [("p0", "DRAFT for https://x/p/p0")]
    assert report.comments_posted == 1


def test_borderline_posts_never_reach_the_gate() -> None:
    """The approval band runs FIRST, so borderline posts trigger no gate side
    effects (e.g. first-comment flagging in a group we'd never comment in)."""
    gate = FakeCommentGate("should_never_be_asked")
    run(
        _ig_adapter(1),
        dedup=FakeIterateOnceDedup(),
        score=lambda post: 0.78,  # candidate + comment band, below auto-approve
        inline_comment=True,
        comment_gate=gate,
    )

    assert gate.checked == [], "the gate ran before the approval band"
