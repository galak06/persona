"""Tests for lib.crew.scout -- run_crew_scout orchestration.

The CrewAI `kickoff()` call is never exercised here: `execute_fn` is always
injected as a plain callable returning a canned `ScoutOutput` (or `None`, to
simulate a failed LLM call). No real Postgres, no real CrewAI/Serper/DeepSeek
network calls, no DATABASE_URL.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from crewai import Agent, Task

from lib.crew.models import IdeaCandidate, ScoutOutput
from lib.crew.scout import _idea_row, run_crew_scout


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


def test_idea_row_shape_matches_gsc_scout_convention() -> None:
    idea = _candidate()
    row = _idea_row(idea, data_sufficient=True)
    assert set(row.keys()) == {
        "category",
        "topic",
        "target_keyword",
        "nalla_context",
        "post_goal",
        "status",
        "input",
    }
    assert row["status"] == "publish"
    assert row["nalla_context"] == idea.reasoning
    assert row["post_goal"] == "topic_discovery"  # web_discovery -> topic_discovery
    parsed_input = json.loads(row["input"])
    assert parsed_input["source"] == "crewai_scout"
    assert parsed_input["opportunity_type"] == "web_discovery"
    assert parsed_input["data_sufficient"] is True


def test_idea_row_post_goal_seo_traffic_for_optimize_and_emerging() -> None:
    for opp_type in ("optimize", "emerging"):
        row = _idea_row(_candidate(opportunity_type=opp_type), data_sufficient=True)
        assert row["post_goal"] == "seo_traffic"


def test_run_crew_scout_inserts_scored_ideas(brand_dir: Path) -> None:
    inserted: list[dict[str, Any]] = []

    def fake_insert_idea(idea: dict[str, Any], *, brand_id: str, brand_name: str) -> str:
        inserted.append({"idea": idea, "brand_id": brand_id, "brand_name": brand_name})
        return f"idea-{len(inserted)}"

    def fake_execute(agent: Agent, task: Task) -> ScoutOutput:
        return ScoutOutput(ideas=[_candidate(), _candidate(topic="Another New Topic")])

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: set(),
        execute_fn=fake_execute,
        dry_run=False,
    )

    assert len(results) == 2
    assert all(idea_id is not None for _, idea_id in results)
    assert len(inserted) == 2
    assert inserted[0]["brand_id"] == brand_dir.name
    assert inserted[0]["brand_name"] == "DogFoodAndFun"


def test_run_crew_scout_skips_existing_topics_case_insensitive(brand_dir: Path) -> None:
    calls: list[Any] = []

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        calls.append(idea)
        return "should-not-be-called"

    def fake_execute(agent: Agent, task: Task) -> ScoutOutput:
        return ScoutOutput(ideas=[_candidate(topic="5 Signs Your Dog Needs A GPS Tracker")])

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: {"5 signs your dog needs a gps tracker"},
        execute_fn=fake_execute,
        dry_run=False,
    )
    assert not calls
    assert len(results) == 1
    assert results[0][1] is None


def test_run_crew_scout_dry_run_never_calls_insert_idea(brand_dir: Path) -> None:
    calls: list[Any] = []

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        calls.append(idea)
        return "unexpected"

    def fake_execute(agent: Agent, task: Task) -> ScoutOutput:
        return ScoutOutput(ideas=[_candidate()])

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: set(),
        execute_fn=fake_execute,
        dry_run=True,
    )
    assert not calls
    assert results
    assert all(idea_id is None for _, idea_id in results)


def test_run_crew_scout_returns_empty_list_when_kickoff_fails(brand_dir: Path) -> None:
    """execute_fn returning None (simulating a failed LLM call / crew kickoff
    exception, per `execute_scout_crew`'s own try/except) must not raise --
    the run just produces zero results, matching this repo's "never crash
    the run" convention."""

    def fake_execute(agent: Agent, task: Task) -> None:
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
        execute_fn=fake_execute,
        dry_run=False,
    )
    assert results == []
    assert not calls


def test_run_crew_scout_returns_empty_list_when_execute_fn_raises(brand_dir: Path) -> None:
    """A raising execute_fn (e.g. a custom one that doesn't catch its own
    errors, unlike the real default `execute_scout_crew`) still must not
    propagate out of run_crew_scout -- defense in depth, same "never crash
    the run" contract as `lib.gsc_scout.run_scout`."""

    def fake_execute(agent: Agent, task: Task) -> ScoutOutput:
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
        execute_fn=fake_execute,
        dry_run=False,
    )
    assert results == []
    assert not calls


def test_execute_scout_crew_is_importable_and_callable() -> None:
    """`run_crew_scout`'s real (non-test) default `execute_fn`. Only checked
    for importability/callability here -- actually calling it would make a
    live CrewAI/DeepSeek/Serper network call, which this file never does."""
    import lib.crew.scout as scout_module

    assert callable(scout_module.execute_scout_crew)
