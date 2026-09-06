"""What a deadline-truncated pass REPORTS — the other half of the slice.

``test_scan_deadline.py`` proves the pass stops in the right place. This
file proves the stop is impossible to miss afterwards, which is the failure
that actually cost a week: a SIGKILLed ig-engager run left no summary at
all, and before that a short run reported the same shape as a complete one.

Covered here:

  * the Telegram summary and the `scan_funnel` log line both declare the
    truncation, and a complete pass keeps its historical shape;
  * `run_ig_scan` still finishes cleanly on a deadline — summary emitted,
    `last_run.json` stamped;
  * the stamped status is `truncated`, not `success`, so `_already_ran_today`
    does not use a partial pass to close the day.

Fakes + a scripted clock only: no browser, no network, nothing sleeps.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from scripts import ig_engager
from scripts.ig_engager import run_ig_scan

from lib.engagement.run_summary import build_summary, log_funnel
from tests.lib.engagement._deadline_fakes import (
    ScriptedClock,
    make_deadline,
    three_source_adapter,
    two_hashtag_adapter,
)
from tests.lib.engagement._pipeline_fakes import FakeLog, run

# --- 1. truncation is visible in both sinks ---------------------------------


def test_summary_and_funnel_both_declare_the_truncation() -> None:
    """A partial pass must never read like a complete one -- that ambiguity is
    the whole reason the funnel exists."""
    report, _d, _rt, _dr = run(
        three_source_adapter(), deadline=make_deadline(ScriptedClock(expire_after=3))
    )
    log = FakeLog()
    log_funnel(report, log)
    summary = build_summary(report, source_label="Hashtags", comment_quota=10, dry_run=False)

    assert "STOPPED: deadline (1/3 sources reached)" in summary
    _level, line = log.lines[0]
    assert line.startswith("scan_funnel platform=instagram sources=1 ")
    assert "stopped_reason=deadline sources_total=3" in line


def test_a_complete_pass_keeps_its_historical_summary_shape() -> None:
    """Nothing is appended when there is nothing to declare."""
    report, _d, _rt, _dr = run(three_source_adapter())
    summary = build_summary(report, source_label="Hashtags", comment_quota=10, dry_run=False)

    assert "STOPPED" not in summary
    assert summary.startswith("Hashtags: 3 | Liked: ")


# --- 2. end to end through run_ig_scan --------------------------------------


@pytest.fixture(autouse=True)
def _stub_skill_drafter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same instance-level stub `test_ig_engager_with_fake` uses: the scan
    injects `ig_engager._DRAFTER`, so a module-level patch would not bite."""

    def _fake_draft(
        *,
        platform: str,
        post_text: str,
        group_or_hashtag: str | None,
        post_url: str,
        site_context: str | None = None,
    ) -> str:
        return f"DRAFT-{post_url or '?'}"

    monkeypatch.setattr(ig_engager._DRAFTER, "draft_comment_for_post", _fake_draft)


def test_ig_scan_finishes_cleanly_and_stamps_truncated_on_deadline(
    ig_environment: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the whole slice: a pass that runs out of budget still
    emits its summary and still writes its state, instead of vanishing."""
    finished: list[str] = []
    monkeypatch.setattr(
        ig_engager, "skill_finished", lambda _skill, summary: finished.append(summary)
    )
    clock = ScriptedClock(expire_after=2)
    monkeypatch.setattr(ig_engager, "deadline_from_env", lambda: make_deadline(clock))

    report = run_ig_scan(adapter=two_hashtag_adapter())

    assert report is not None
    assert report.stopped_reason == "deadline"
    assert (report.sources_visited, report.sources_total) == (1, 2)
    # The summary a human reads reached Telegram, truncation and all.
    assert len(finished) == 1
    assert "STOPPED: deadline (1/2 sources reached)" in finished[0]
    # ...and the state a machine reads was written.
    stamp = json.loads(Path(ig_environment["last_run_path"]).read_text())["ig_engager"]
    assert stamp["status"] == "truncated"
    assert stamp["stopped_reason"] == "deadline"
    assert stamp["hashtags_scanned"] == 1
    assert stamp["hashtags_due"] == 2


def test_a_truncated_pass_does_not_close_the_day(
    ig_environment: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_already_ran_today` trips only on a COMPLETE pass.

    Stamping a truncated run "success" would suppress every later attempt
    that day, so the hashtags it never reached would wait until tomorrow.
    The safer default is to let a second attempt through; `ScanDedup` makes
    it cheap by skipping every post the first pass already opened.
    """
    clock = ScriptedClock(expire_after=2)
    monkeypatch.setattr(ig_engager, "deadline_from_env", lambda: make_deadline(clock))
    run_ig_scan(adapter=two_hashtag_adapter())

    last_run = json.loads(Path(ig_environment["last_run_path"]).read_text())
    assert ig_engager._already_ran_today(last_run) is False


def test_a_complete_pass_still_closes_the_day(
    ig_environment: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard's existing behaviour is untouched for a full pass."""
    monkeypatch.setattr(ig_engager, "deadline_from_env", lambda: None)
    run_ig_scan(adapter=two_hashtag_adapter())

    last_run = json.loads(Path(ig_environment["last_run_path"]).read_text())
    assert last_run["ig_engager"]["status"] == "success"
    assert last_run["ig_engager"]["stopped_reason"] is None
    assert ig_engager._already_ran_today(last_run) is True
