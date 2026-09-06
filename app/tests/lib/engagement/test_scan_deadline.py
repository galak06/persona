"""The internal scan deadline: the budget, and where a pass stops on it.

`scripts/task_worker.py` runs every flow under `subprocess.run(timeout=)`,
which SIGKILLs an overrunning child. `scripts/ig_engager.py` went seven days
(2026-08-29 -> 2026-09-05) without emitting its terminal `scan_funnel` line
for that reason, and because a killed pass never stamps `last_run.json`,
`_already_ran_today` stopped suppressing the next tick too.

This file locks the two halves of the fallback that replaces the kill:

  1. the budget itself (`deadline_from_env`) and its safety margin,
  2. the pipeline stopping at a source or post boundary, never mid-action.

What a stopped pass then REPORTS lives in ``test_scan_truncation.py`` (the
two are one slice, split only by the 300-line cap).

Everything runs against the pipeline fakes and a scripted clock: no browser,
no network, no real timeouts -- nothing here sleeps.
"""
# ruff: noqa: S101

from __future__ import annotations

import pytest

from lib.engagement.scan_deadline import (
    RESERVE_SECONDS,
    DeadlineAwareRateTracker,
    ScanDeadline,
    deadline_from_env,
)
from tests.lib.engagement._deadline_fakes import (
    HEALTHY_PASS_SECONDS,
    ScriptedClock,
    make_deadline,
    three_source_adapter,
)
from tests.lib.engagement._pipeline_fakes import FakeLog, FakeRateTracker, run

# --- 1. the budget and its margin -------------------------------------------


def test_budget_reserves_a_fixed_margin_before_the_worker_fuse() -> None:
    """The deadline lands RESERVE_SECONDS before the worker would SIGKILL."""
    deadline = deadline_from_env({"FLOW_TIMEOUT_SECONDS": "1800"}, now=lambda: 100.0)

    assert deadline is not None
    assert deadline.reserve_seconds == RESERVE_SECONDS
    assert deadline.deadline_at == 100.0 + 1800.0 - RESERVE_SECONDS
    assert deadline.budget_seconds == 1800.0


def test_a_healthy_full_pass_never_reaches_the_deadline() -> None:
    """The margin is a fallback, not a shortener.

    A pass that would finish on its own must not be cut short: the 2026-09-05
    run took 1512s of the 1800s fuse, and the deadline sits at 1620s.
    """
    deadline = deadline_from_env({"FLOW_TIMEOUT_SECONDS": "1800"}, now=lambda: 0.0)

    assert deadline is not None
    assert deadline.deadline_at > HEALTHY_PASS_SECONDS, (
        "a run of the observed healthy length must finish before the deadline"
    )


@pytest.mark.parametrize("raw", ["", "   ", "not-a-number", "0", "-5"])
def test_no_usable_budget_means_no_deadline(raw: str) -> None:
    """Absent, malformed or non-positive budgets disable the stop entirely.

    A manual CLI run declares no budget, and a broken value must never
    truncate a pass that would otherwise have finished.
    """
    assert deadline_from_env({"FLOW_TIMEOUT_SECONDS": raw}) is None
    assert deadline_from_env({}) is None


def test_a_tiny_budget_gives_up_half_of_itself_not_all_of_it() -> None:
    """A budget under twice the reserve still leaves room to work in."""
    deadline = deadline_from_env({"FLOW_TIMEOUT_SECONDS": "120"}, now=lambda: 0.0)

    assert deadline is not None
    assert deadline.reserve_seconds == 60.0
    assert deadline.deadline_at == 60.0


def test_expired_flips_exactly_at_the_deadline() -> None:
    now = [999.0]
    deadline = ScanDeadline(
        deadline_at=1_000.0, budget_seconds=1800.0, reserve_seconds=180.0, now=lambda: now[0]
    )

    assert not deadline.expired()
    assert deadline.remaining() == 1.0
    now[0] = 1_000.0
    assert deadline.expired()


# --- 2. the pacing sleep the reserve does not have to cover ------------------


def test_pacing_sleep_still_happens_while_time_remains() -> None:
    """Nothing about the normal cadence changes before the deadline."""
    inner = FakeRateTracker()
    tracker = DeadlineAwareRateTracker(inner, make_deadline(ScriptedClock(expire_after=10)))

    tracker.wait_random_delay("instagram", "comment")

    assert inner.delays == [("instagram", "comment")]


def test_pacing_sleep_is_dropped_once_the_deadline_has_passed() -> None:
    """The 120-180s comment delay paces the NEXT comment; a pass that is
    shutting down has none, and that sleep would eat the whole reserve."""
    inner = FakeRateTracker()
    tracker = DeadlineAwareRateTracker(inner, make_deadline(ScriptedClock(expire_after=0)))

    tracker.wait_random_delay("instagram", "comment")

    assert inner.delays == []


def test_budgets_are_never_relaxed_by_the_deadline() -> None:
    """Only the sleep is conditional: quota reads and spends pass straight
    through, so an ending run can never buy itself extra actions."""
    inner = FakeRateTracker(comments_left=1)
    tracker = DeadlineAwareRateTracker(inner, make_deadline(ScriptedClock(expire_after=0)))

    assert tracker.can_act("instagram", "comment") is True
    tracker.record_action("instagram", "comment")

    assert inner.recorded == [("instagram", "comment")]
    assert tracker.can_act("instagram", "comment") is False


def test_a_run_without_a_deadline_paces_as_before() -> None:
    inner = FakeRateTracker()
    DeadlineAwareRateTracker(inner, None).wait_random_delay("instagram", "comment")

    assert inner.delays == [("instagram", "comment")]


# --- 3. where the pipeline actually stops ------------------------------------


def test_scan_stops_at_the_next_source_boundary() -> None:
    """Source 1 runs to completion; sources 2 and 3 are never opened.

    Checkpoint order for one full source is: the source boundary, then one
    read per post. Expiring after the first three reads lands the stop on
    source 2's boundary.
    """
    adapter = three_source_adapter()

    report, _d, _rt, _dr = run(adapter, deadline=make_deadline(ScriptedClock(expire_after=3)))

    assert report.sources_visited == 1
    assert report.sources_total == 3
    assert report.stopped_reason == "deadline"
    assert report.posts_scanned == 2, "the source it entered was finished, not abandoned"
    assert {p.post_id for p in adapter.likes_attempted} == {"s1-p1", "s1-p2"}


def test_scan_stops_between_posts_inside_a_source() -> None:
    """A single source can outrun the budget on its own, so the post loop
    checks too -- but only BETWEEN posts, never mid-visit."""
    adapter = three_source_adapter()

    report, _d, _rt, _dr = run(adapter, deadline=make_deadline(ScriptedClock(expire_after=2)))

    assert report.sources_visited == 1
    assert report.posts_scanned == 1
    assert report.stopped_reason == "deadline"
    assert [p.post_id for p in adapter.likes_attempted] == ["s1-p1"], (
        "the post already in flight completed; the next one never started"
    )


def test_a_complete_pass_reports_no_stop_reason() -> None:
    """The deadline is inert on a run that finishes inside it."""
    report, _d, _rt, _dr = run(
        three_source_adapter(), deadline=make_deadline(ScriptedClock(expire_after=999))
    )

    assert report.sources_visited == 3
    assert report.sources_total == 3
    assert report.stopped_reason is None
    assert report.posts_scanned == 6


def test_the_stop_is_logged_as_a_warning_once() -> None:
    """One warning, not one per checkpoint -- the operator needs to find the
    truncation without reading the funnel arithmetic."""
    log = FakeLog()
    run(three_source_adapter(), deadline=make_deadline(ScriptedClock(expire_after=3)), log=log)

    stops = [line for level, line in log.lines if level == "warning" and "deadline" in line]
    assert len(stops) == 1
    assert "scan_stopped_at_deadline platform=instagram" in stops[0]
    assert "sources_visited=1 sources_total=3" in stops[0]
