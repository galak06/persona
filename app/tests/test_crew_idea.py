"""Tests for lib.crew.idea -- content-idea synthesis agent construction,
prompt building, and output parsing.

No CrewAI kickoff, no network, no DATABASE_URL. `execute_idea_crew` is only
checked for importability/callability (same convention as
`test_crew_scout.py::test_execute_scout_crew_is_importable_and_callable`) --
actually calling it would make a live CrewAI/DeepSeek network call. The
JSON-parsing helper it wraps (`_parse_structured_output`) is pure and tested
directly.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from typing import Any

import pytest

from lib.crew.idea.agent import build_idea_agent, build_idea_task
from lib.crew.idea.execute import _parse_structured_output
from lib.crew.idea.prompts import build_idea_task_description, serialize_trend_signals
from lib.crew.models import ScoutOutput
from lib.crew.trends.models import TrendSignal


def _signal(**overrides: Any) -> TrendSignal:
    defaults: dict[str, Any] = {
        "keyword": "dog gps tracker",
        "category": "GPS/Gear",
        "opportunity_type": "web_discovery",
        "score": 82.0,
        "reason": "Rising forum discussion about lost-dog GPS use.",
    }
    defaults.update(overrides)
    return TrendSignal(**defaults)


# ── serialize_trend_signals ──────────────────────────────────────────────────


def test_serialize_trend_signals_round_trips_expected_fields() -> None:
    signals = [
        _signal(
            keyword="dog food",
            category="content_analysis:primary_keywords",
            opportunity_type="optimize",
            score=82.5,
            reason="optimize opportunity for 'dog food'",
        )
    ]
    parsed = json.loads(serialize_trend_signals(signals))
    assert parsed == [
        {
            "keyword": "dog food",
            "category": "content_analysis:primary_keywords",
            "opportunity_type": "optimize",
            "score": 82.5,
            "reason": "optimize opportunity for 'dog food'",
        }
    ]


def test_serialize_trend_signals_empty_list_is_empty_json_array() -> None:
    assert serialize_trend_signals([]) == "[]"


# ── build_idea_task_description ──────────────────────────────────────────────


def test_build_idea_task_description_includes_all_sections() -> None:
    description = build_idea_task_description(
        identity="Brand: DogFoodAndFun",
        voice="Authentic, peer-to-peer.",
        seed_keywords="dog food, gps tracker",
        trends_json=serialize_trend_signals([_signal()]),
        top_n=7,
    )
    assert "Brand: DogFoodAndFun" in description
    assert "Authentic, peer-to-peer." in description
    assert "dog food, gps tracker" in description
    assert "dog gps tracker" in description  # from trends_json
    assert "at most 7 ideas" in description


def test_build_idea_task_description_missing_voice_and_seed_keywords_fallback() -> None:
    description = build_idea_task_description(
        identity="Brand: X",
        voice="",
        seed_keywords="",
        trends_json="[]",
        top_n=10,
    )
    assert "(no voice guide available)" in description
    assert "(none on file)" in description


# ── build_idea_agent / build_idea_task ───────────────────────────────────────


def test_build_idea_agent_has_no_tools() -> None:
    agent = build_idea_agent()
    assert agent.role == "Content Idea Synthesizer"
    assert agent.tools == []


def test_build_idea_task_has_no_output_pydantic() -> None:
    agent = build_idea_agent()
    task = build_idea_task(agent, "some description")
    assert task.output_pydantic is None
    assert task.description == "some description"


# ── _parse_structured_output (pure JSON-parsing helper) ─────────────────────


def test_parse_structured_output_none_on_empty_input() -> None:
    assert _parse_structured_output(None, ScoutOutput, event="test_event") is None
    assert _parse_structured_output("", ScoutOutput, event="test_event") is None
    assert _parse_structured_output("   ", ScoutOutput, event="test_event") is None


def test_parse_structured_output_none_on_invalid_json() -> None:
    assert _parse_structured_output("not json at all", ScoutOutput, event="test_event") is None


def test_parse_structured_output_none_on_schema_mismatch() -> None:
    # IdeaCandidate requires topic/target_keyword/category/reasoning/
    # opportunity_type/priority_score -- an entry missing all but "topic"
    # fails validation.
    payload = json.dumps({"ideas": [{"topic": "Only a topic, nothing else"}]})
    assert _parse_structured_output(payload, ScoutOutput, event="test_event") is None


def test_parse_structured_output_valid_json_round_trips() -> None:
    payload = json.dumps(
        {
            "ideas": [
                {
                    "topic": "5 Signs Your Dog Needs a GPS Tracker",
                    "target_keyword": "dog gps tracker signs",
                    "category": "GPS/Gear",
                    "reasoning": "Grounded in a real trend signal.",
                    "opportunity_type": "web_discovery",
                    "priority_score": 88.0,
                }
            ]
        }
    )
    output = _parse_structured_output(payload, ScoutOutput, event="test_event")
    assert output is not None
    assert len(output.ideas) == 1
    assert output.ideas[0].topic == "5 Signs Your Dog Needs a GPS Tracker"


def test_parse_structured_output_extracts_json_from_surrounding_reasoning_text() -> None:
    """Regression test for a real DeepSeek run: ~30KB of visible
    chain-of-thought reasoning preceded the final JSON object, so the raw
    text didn't start with `{` and a direct `json.loads` on the whole
    string failed immediately with `Expecting value: line 1 column 1 (char
    0)`. `_parse_structured_output` must locate and parse the `{...}`
    object embedded in the reasoning text instead of giving up."""
    payload = {
        "ideas": [
            {
                "topic": "5 Signs Your Dog Needs a GPS Tracker",
                "target_keyword": "dog gps tracker signs",
                "category": "GPS/Gear",
                "reasoning": "Grounded in a real trend signal.",
                "opportunity_type": "web_discovery",
                "priority_score": 88.0,
            }
        ]
    }
    raw = (
        "Some reasoning text first, weighing multiple candidate topics against "
        "the supplied trend signals.\n"
        + json.dumps(payload)
        + "\n\nMore trailing reasoning about why this final JSON is the right pick."
    )
    output = _parse_structured_output(raw, ScoutOutput, event="test_event")
    assert output is not None
    assert len(output.ideas) == 1
    assert output.ideas[0].topic == "5 Signs Your Dog Needs a GPS Tracker"


def test_parse_structured_output_prefers_final_json_object_over_earlier_draft() -> None:
    """Regression test for the exact shape hit in a real dry-run against
    dogfoodandfun: DeepSeek emitted a complete, syntactically-valid JSON
    object mid-reasoning (inside a ` ```json ` fence), then MORE prose
    ("Wait, let me verify this is complete... The output looks good."),
    then a second, final JSON object with the real answer. A naive
    first-`{`-to-last-`}` slice captures both objects plus the prose
    between them (not valid JSON); the fix must instead find each
    balanced object independently and use the last one that parses --
    the model's actual final answer, not the superseded draft."""
    draft_payload = {
        "ideas": [
            {
                "topic": "Draft topic -- superseded",
                "target_keyword": "draft keyword",
                "category": "GPS/Gear",
                "reasoning": "this is the earlier draft, not the final answer",
                "opportunity_type": "web_discovery",
                "priority_score": 10.0,
            }
        ]
    }
    final_payload = {
        "ideas": [
            {
                "topic": "5 Signs Your Dog Needs a GPS Tracker",
                "target_keyword": "dog gps tracker signs",
                "category": "GPS/Gear",
                "reasoning": "Grounded in a real trend signal.",
                "opportunity_type": "web_discovery",
                "priority_score": 88.0,
            }
        ]
    }
    raw = (
        "Let me compile the final JSON.\n\n```json\n"
        + json.dumps(draft_payload, indent=2)
        + "\n```\n\n"
        "Wait, let me verify this is complete and validates against the schema... "
        "The output looks good.\n\n" + json.dumps(final_payload)
    )
    output = _parse_structured_output(raw, ScoutOutput, event="test_event")
    assert output is not None
    assert len(output.ideas) == 1
    assert output.ideas[0].topic == "5 Signs Your Dog Needs a GPS Tracker"


def test_parse_structured_output_none_and_logs_when_no_json_object_anywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Genuinely malformed output (no `{...}` substring parses as valid
    JSON) must not be silently swallowed -- still logs
    `{event}_json_decode_failed` with the full raw text and returns `None`,
    exactly as before this fix."""
    import lib.crew.idea.execute as execute_module

    warnings: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        execute_module.logger,
        "warning",
        lambda event, **kwargs: warnings.append((event, kwargs)),
    )

    raw = "Let me think about this some more. I am not ready to answer yet."
    output = execute_module._parse_structured_output(raw, ScoutOutput, event="test_event")

    assert output is None
    assert len(warnings) == 1
    event, kwargs = warnings[0]
    assert event == "test_event_json_decode_failed"
    assert kwargs["raw_output"] == raw


# ── execute_idea_crew ────────────────────────────────────────────────────────


def test_execute_idea_crew_is_importable_and_callable() -> None:
    """Only checked for importability/callability -- actually calling it
    would make a live CrewAI/DeepSeek network call, which this file never
    does (see module docstring)."""
    import lib.crew.idea.execute as execute_module

    assert callable(execute_module.execute_idea_crew)
