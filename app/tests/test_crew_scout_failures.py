"""Failure-mode tests for lib.crew.scout -- run_crew_scout orchestration.

Split out of test_crew_scout.py purely for file-size discipline. Covers the
"never crash the run" contract: a failed/raising trends or idea stage must
never propagate out of run_crew_scout and must never invoke insert_idea_fn.

Small fixtures/helpers are duplicated here rather than imported from
test_crew_scout.py, matching this repo's existing test-split convention (see
the test_crew_draft*.py family).
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


def test_run_crew_scout_returns_empty_list_when_trends_stage_fails(brand_dir: Path) -> None:
    """trends_execute_fn returning None (simulating a failed LLM call / crew
    kickoff exception, per `execute_trends_crew`'s own try/except) must not
    raise -- the run just produces zero results, matching this repo's "never
    crash the run" convention. The Idea stage must never even be invoked."""

    idea_calls: list[Any] = []

    def fake_trends_execute(agent: Agent, task: Task) -> None:
        return None

    def fake_idea_execute(agent: Agent, task: Task) -> ScoutOutput:
        idea_calls.append((agent, task))
        return ScoutOutput(ideas=[_candidate()])

    calls: list[Any] = []

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        calls.append(idea)
        return "should-not-be-called"

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: set(),
        trends_execute_fn=fake_trends_execute,
        idea_execute_fn=fake_idea_execute,
        instagram_trends_fn=_no_instagram_trends,
        dry_run=False,
    )
    assert results == []
    assert not calls
    assert not idea_calls


def test_run_crew_scout_returns_empty_list_when_kickoff_fails(brand_dir: Path) -> None:
    """idea_execute_fn returning None (simulating a failed LLM call / crew
    kickoff exception, per `execute_idea_crew`'s own try/except) must not
    raise -- the run just produces zero results, matching this repo's "never
    crash the run" convention."""

    def fake_trends_execute(agent: Agent, task: Task) -> TrendsOutput:
        return TrendsOutput(signals=[_signal()])

    def fake_idea_execute(agent: Agent, task: Task) -> None:
        return None

    calls: list[Any] = []

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        calls.append(idea)
        return "should-not-be-called"

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: set(),
        trends_execute_fn=fake_trends_execute,
        idea_execute_fn=fake_idea_execute,
        instagram_trends_fn=_no_instagram_trends,
        dry_run=False,
    )
    assert results == []
    assert not calls


def test_run_crew_scout_continues_batch_when_insert_idea_fn_raises(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`insert_idea_fn` raising for one idea in a multi-idea batch must not
    abort the batch -- same "never crash the run" contract as the
    trends/idea `execute_fn` wrappers above, but scoped to a single idea.
    The failing idea's result is `(idea, None)`, exactly like a normal
    failed insert; the other idea in the batch is still processed/inserted
    normally, and `crew_scout_run_complete` still fires at the end (the
    loop doesn't abort early)."""
    import lib.crew.scout as scout_module

    warnings: list[tuple[str, dict[str, Any]]] = []
    infos: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        scout_module.logger, "warning", lambda event, **kw: warnings.append((event, kw))
    )
    monkeypatch.setattr(
        scout_module.logger, "info", lambda event, **kw: infos.append((event, kw))
    )

    idea_a = _candidate(topic="Idea A Raises On Insert")
    idea_b = _candidate(topic="Idea B Inserts Fine")

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        if idea["topic"] == idea_a.topic:
            raise RuntimeError("db boom")
        return "idea-b-id"

    def fake_trends_execute(agent: Agent, task: Task) -> TrendsOutput:
        return TrendsOutput(signals=[_signal()])

    def fake_idea_execute(agent: Agent, task: Task) -> ScoutOutput:
        return ScoutOutput(ideas=[idea_a, idea_b])

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: set(),
        trends_execute_fn=fake_trends_execute,
        idea_execute_fn=fake_idea_execute,
        instagram_trends_fn=_no_instagram_trends,
        update_status_fn=lambda *_: True,
        dry_run=False,
    )

    assert results == [(idea_a, None), (idea_b, "idea-b-id")]

    raised = [(event, kw) for event, kw in warnings if event == "crew_scout_insert_idea_fn_raised"]
    assert len(raised) == 1
    assert raised[0][1]["topic"] == idea_a.topic

    assert any(event == "crew_scout_run_complete" for event, _ in infos)


def test_run_crew_scout_returns_empty_list_when_trends_execute_fn_raises(
    brand_dir: Path,
) -> None:
    """A raising trends_execute_fn (e.g. a custom one that doesn't catch its
    own errors, unlike the real default `execute_trends_crew`) still must not
    propagate out of run_crew_scout -- defense in depth, same "never crash
    the run" contract as `lib.gsc_scout.run_scout`. The Idea stage must never
    even be invoked."""

    idea_calls: list[Any] = []

    def fake_trends_execute(agent: Agent, task: Task) -> TrendsOutput:
        raise RuntimeError("network boom")

    def fake_idea_execute(agent: Agent, task: Task) -> ScoutOutput:
        idea_calls.append((agent, task))
        return ScoutOutput(ideas=[_candidate()])

    calls: list[Any] = []

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        calls.append(idea)
        return "should-not-be-called"

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: set(),
        trends_execute_fn=fake_trends_execute,
        idea_execute_fn=fake_idea_execute,
        instagram_trends_fn=_no_instagram_trends,
        dry_run=False,
    )
    assert results == []
    assert not calls
    assert not idea_calls


def test_run_crew_scout_returns_empty_list_when_idea_execute_fn_raises(brand_dir: Path) -> None:
    """A raising idea_execute_fn (e.g. a custom one that doesn't catch its
    own errors, unlike the real default `execute_idea_crew`) still must not
    propagate out of run_crew_scout -- defense in depth, same "never crash
    the run" contract as `lib.gsc_scout.run_scout`."""

    def fake_trends_execute(agent: Agent, task: Task) -> TrendsOutput:
        return TrendsOutput(signals=[_signal()])

    def fake_idea_execute(agent: Agent, task: Task) -> ScoutOutput:
        raise RuntimeError("network boom")

    calls: list[Any] = []

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        calls.append(idea)
        return "should-not-be-called"

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: set(),
        trends_execute_fn=fake_trends_execute,
        idea_execute_fn=fake_idea_execute,
        instagram_trends_fn=_no_instagram_trends,
        dry_run=False,
    )
    assert results == []
    assert not calls
