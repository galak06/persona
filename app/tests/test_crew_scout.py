"""Tests for lib.crew.scout -- run_crew_scout orchestration.

The CrewAI `kickoff()` call is never exercised here: `trends_execute_fn` and
`idea_execute_fn` are always injected as plain callables returning a canned
`TrendsOutput`/`ScoutOutput` (or `None`, to simulate a failed LLM call).
`instagram_trends_fn` is always injected too, so no real Playwright/network
call is ever made. No real Postgres, no real CrewAI/Serper/DeepSeek network
calls, no DATABASE_URL.

Failure-mode tests (kickoff/stage failures and exceptions) live in
`test_crew_scout_failures.py`.
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


def test_idea_row_post_goal_topic_discovery_for_instagram_trend() -> None:
    row = _idea_row(_candidate(opportunity_type="instagram_trend"), data_sufficient=True)
    assert row["post_goal"] == "topic_discovery"


def test_run_crew_scout_inserts_scored_ideas(brand_dir: Path) -> None:
    inserted: list[dict[str, Any]] = []

    def fake_insert_idea(idea: dict[str, Any], *, brand_id: str, brand_name: str) -> str:
        inserted.append({"idea": idea, "brand_id": brand_id, "brand_name": brand_name})
        return f"idea-{len(inserted)}"

    def fake_trends_execute(agent: Agent, task: Task) -> TrendsOutput:
        return TrendsOutput(signals=[_signal()])

    def fake_idea_execute(agent: Agent, task: Task) -> ScoutOutput:
        return ScoutOutput(ideas=[_candidate(), _candidate(topic="Another New Topic")])

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

    def fake_trends_execute(agent: Agent, task: Task) -> TrendsOutput:
        return TrendsOutput(signals=[_signal()])

    def fake_idea_execute(agent: Agent, task: Task) -> ScoutOutput:
        return ScoutOutput(ideas=[_candidate(topic="5 Signs Your Dog Needs A GPS Tracker")])

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: {"5 signs your dog needs a gps tracker"},
        trends_execute_fn=fake_trends_execute,
        idea_execute_fn=fake_idea_execute,
        instagram_trends_fn=_no_instagram_trends,
        dry_run=False,
    )
    assert not calls
    assert len(results) == 1
    assert results[0][1] is None


def test_run_crew_scout_skips_fuzzy_duplicate_topic(brand_dir: Path) -> None:
    """A candidate topic that isn't an exact string match but is a close
    fuzzy match (singular/plural rewording) to an existing topic must still
    be skipped -- the new `is_similar_topic` behavior, distinct from the
    exact-match case covered above."""

    calls: list[Any] = []

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        calls.append(idea)
        return "should-not-be-called"

    def fake_trends_execute(agent: Agent, task: Task) -> TrendsOutput:
        return TrendsOutput(signals=[_signal()])

    def fake_idea_execute(agent: Agent, task: Task) -> ScoutOutput:
        return ScoutOutput(ideas=[_candidate(topic="Homemade Dog Food Recipe for Beginners")])

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: {"homemade dog food recipes for beginners"},
        trends_execute_fn=fake_trends_execute,
        idea_execute_fn=fake_idea_execute,
        instagram_trends_fn=_no_instagram_trends,
        dry_run=False,
    )
    assert not calls
    assert len(results) == 1
    assert results[0][1] is None


def test_run_crew_scout_does_not_skip_dissimilar_topic(brand_dir: Path) -> None:
    """Companion to the fuzzy-duplicate test above: a topic that shares some
    words with an existing one but is substantively different must NOT be
    skipped -- guards against false positives in the fuzzy check."""

    inserted: list[dict[str, Any]] = []

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        inserted.append(idea)
        return "idea-1"

    def fake_trends_execute(agent: Agent, task: Task) -> TrendsOutput:
        return TrendsOutput(signals=[_signal()])

    def fake_idea_execute(agent: Agent, task: Task) -> ScoutOutput:
        return ScoutOutput(ideas=[_candidate(topic="Best GPS Trackers for Anxious Dogs")])

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: {"homemade dog food recipes for beginners"},
        trends_execute_fn=fake_trends_execute,
        idea_execute_fn=fake_idea_execute,
        instagram_trends_fn=_no_instagram_trends,
        update_status_fn=lambda *_: True,
        dry_run=False,
    )
    assert len(inserted) == 1
    assert results[0][1] == "idea-1"


def test_run_crew_scout_dry_run_never_calls_insert_idea(brand_dir: Path) -> None:
    calls: list[Any] = []

    def fake_insert_idea(idea: dict[str, Any], **_: Any) -> str:
        calls.append(idea)
        return "unexpected"

    def fake_trends_execute(agent: Agent, task: Task) -> TrendsOutput:
        return TrendsOutput(signals=[_signal()])

    def fake_idea_execute(agent: Agent, task: Task) -> ScoutOutput:
        return ScoutOutput(ideas=[_candidate()])

    results = run_crew_scout(
        brand_dir,
        repo=_FakeRepo([]),  # type: ignore[arg-type]
        insert_idea_fn=fake_insert_idea,
        existing_topics_fn=lambda **_: set(),
        trends_execute_fn=fake_trends_execute,
        idea_execute_fn=fake_idea_execute,
        instagram_trends_fn=_no_instagram_trends,
        dry_run=True,
    )
    assert not calls
    assert results
    assert all(idea_id is None for _, idea_id in results)
