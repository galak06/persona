"""Auto-approve tests for lib.crew.scout -- run_crew_scout orchestration.

Split out of test_crew_scout.py purely for file-size discipline. Covers the
`auto_approve_threshold` / `update_status_fn` behavior: ideas scoring at or
above the threshold get `update_status_fn(idea_id, "approved")` called right
after insert, unless the run is a dry run (no insert happens at all).

Small fixtures/helpers are duplicated here rather than imported from
test_crew_scout.py, matching this repo's existing test-split convention (see
the test_crew_draft*.py family and test_crew_scout_failures.py).
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from crewai import Agent, Task

from lib.crew.models import IdeaCandidate, ScoutOutput
from lib.crew.scout import run_crew_scout
from lib.crew.trends.models import TrendSignal, TrendsOutput


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def brand_dir(tmp_path: Path) -> Path:
    _write_json(
        tmp_path / "config.json",
        {
            "site": {"name": "DogFoodAndFun", "url": "https://dogfoodandfun.com"},
            "content_analysis": {
                "keywords": {
                    "primary_keywords": ["dog food", "homemade"],
                    "competitor_mentions": ["some-competitor"],
                }
            },
        },
    )
    return tmp_path


class _FakeRepo:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def list_for_brand(self, brand_id: str, *, limit: int = 10_000) -> list[dict[str, Any]]:
        return self._rows


def _candidate(**overrides: Any) -> IdeaCandidate:
    defaults: dict[str, Any] = {
        "topic": "5 Signs Your Dog Needs a GPS Tracker",
        "target_keyword": "dog gps tracker signs",
        "category": "GPS/Gear",
        "reasoning": "Found via web search: rising forum discussion about lost-dog GPS use.",
        "opportunity_type": "web_discovery",
        "priority_score": 88.0,
    }
    defaults.update(overrides)
    return IdeaCandidate(**defaults)


def _signal(**overrides: Any) -> TrendSignal:
    defaults: dict[str, Any] = {
        "keyword": "dog gps tracker",
        "category": "GPS/Gear",
        "opportunity_type": "web_discovery",
        "score": 80.0,
        "reason": "Rising forum discussion about lost-dog GPS use.",
    }
    defaults.update(overrides)
    return TrendSignal(**defaults)


def _no_instagram_trends(_brand_dir: Path) -> str | None:
    return None


def test_run_crew_scout_auto_approves_high_scoring_idea(brand_dir: Path) -> None:
    """priority_score >= the default auto_approve_threshold (85.0) triggers
    update_status_fn(idea_id, "approved") right after insert."""

    approve_calls: list[tuple[str, str]] = []

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        return "idea-1"

    def fake_update_status(idea_id: str, status: str) -> bool:
        approve_calls.append((idea_id, status))
        return True

    def fake_trends_execute(agent: Agent, task: Task) -> TrendsOutput:
        return TrendsOutput(signals=[_signal()])

    def fake_idea_execute(agent: Agent, task: Task) -> ScoutOutput:
        return ScoutOutput(ideas=[_candidate(priority_score=90.0)])

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: set(),
        trends_execute_fn=fake_trends_execute,
        idea_execute_fn=fake_idea_execute,
        instagram_trends_fn=_no_instagram_trends,
        update_status_fn=fake_update_status,
        dry_run=False,
    )
    assert approve_calls == [("idea-1", "approved")]
    assert len(results) == 1
    assert results[0][1] == "idea-1"


def test_run_crew_scout_does_not_auto_approve_below_threshold(brand_dir: Path) -> None:
    """priority_score below the default auto_approve_threshold (85.0) still
    gets inserted, but update_status_fn is never called."""

    approve_calls: list[tuple[str, str]] = []

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        return "idea-1"

    def fake_update_status(idea_id: str, status: str) -> bool:
        approve_calls.append((idea_id, status))
        return True

    def fake_trends_execute(agent: Agent, task: Task) -> TrendsOutput:
        return TrendsOutput(signals=[_signal()])

    def fake_idea_execute(agent: Agent, task: Task) -> ScoutOutput:
        return ScoutOutput(ideas=[_candidate(priority_score=50.0)])

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: set(),
        trends_execute_fn=fake_trends_execute,
        idea_execute_fn=fake_idea_execute,
        instagram_trends_fn=_no_instagram_trends,
        update_status_fn=fake_update_status,
        dry_run=False,
    )
    assert not approve_calls
    assert results[0][1] == "idea-1"


def test_run_crew_scout_logs_auto_approve_raised_without_losing_insert(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`update_status_fn` raising during auto-approve must not null out the
    already-successful insert -- the idea's result stays `(idea, idea_id)`.
    Logs `crew_scout_idea_auto_approve_raised` with idea_id/topic/error;
    `crew_scout_idea_auto_approve_failed` and `crew_scout_idea_auto_approved`
    must NOT also fire for the same idea (the three are mutually
    exclusive)."""
    import lib.crew.scout as scout_module

    warnings: list[tuple[str, dict[str, Any]]] = []
    infos: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        scout_module.logger, "warning", lambda event, **kw: warnings.append((event, kw))
    )
    monkeypatch.setattr(
        scout_module.logger, "info", lambda event, **kw: infos.append((event, kw))
    )

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        return "idea-1"

    def fake_update_status(idea_id: str, status: str) -> bool:
        raise RuntimeError("status update boom")

    def fake_trends_execute(agent: Agent, task: Task) -> TrendsOutput:
        return TrendsOutput(signals=[_signal()])

    def fake_idea_execute(agent: Agent, task: Task) -> ScoutOutput:
        return ScoutOutput(ideas=[_candidate(priority_score=90.0)])

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: set(),
        trends_execute_fn=fake_trends_execute,
        idea_execute_fn=fake_idea_execute,
        instagram_trends_fn=_no_instagram_trends,
        update_status_fn=fake_update_status,
        dry_run=False,
    )

    assert len(results) == 1
    assert results[0][1] == "idea-1"

    raised = [
        (event, kw) for event, kw in warnings if event == "crew_scout_idea_auto_approve_raised"
    ]
    assert len(raised) == 1
    assert raised[0][1]["idea_id"] == "idea-1"
    assert raised[0][1]["topic"] == results[0][0].topic

    assert not any(event == "crew_scout_idea_auto_approve_failed" for event, _ in warnings)
    assert not any(event == "crew_scout_idea_auto_approved" for event, _ in infos)


def test_run_crew_scout_dry_run_never_auto_approves(brand_dir: Path) -> None:
    """A dry run never inserts (so there's no idea_id to update) and the
    auto-approve branch sits below the dry-run `continue` -- update_status_fn
    must never be called even for a high-scoring idea."""

    approve_calls: list[tuple[str, str]] = []

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        return "unexpected"

    def fake_update_status(idea_id: str, status: str) -> bool:
        approve_calls.append((idea_id, status))
        return True

    def fake_trends_execute(agent: Agent, task: Task) -> TrendsOutput:
        return TrendsOutput(signals=[_signal()])

    def fake_idea_execute(agent: Agent, task: Task) -> ScoutOutput:
        return ScoutOutput(ideas=[_candidate(priority_score=95.0)])

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: set(),
        trends_execute_fn=fake_trends_execute,
        idea_execute_fn=fake_idea_execute,
        instagram_trends_fn=_no_instagram_trends,
        update_status_fn=fake_update_status,
        dry_run=True,
    )
    assert not approve_calls
    assert results
    assert all(idea_id is None for _, idea_id in results)
