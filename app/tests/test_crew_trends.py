"""Tests for lib.crew.trends -- market-trend scout agent construction,
prompt building, and output parsing.

No CrewAI kickoff, no network, no Playwright, no DATABASE_URL.
`execute_trends_crew` is only checked for importability/callability (same
convention as `test_crew_scout.py::test_execute_scout_crew_is_importable_and_
callable`) -- actually calling it would make a live CrewAI/DeepSeek/Serper
network call, which this file never does. The JSON-parsing helper it wraps
(`_parse_structured_output`) is pure and tested directly.

`fetch_instagram_trends_text` gets the same importability/callability-only
smoke test below (calling it for real would launch a real Playwright browser
against instagram.com). Its `page.goto(...)` failure-logging behavior is
still independently testable, though, by monkeypatching the `BrowserSession`
name `instagram_scraper.py` imports at module scope with a plain fake context
manager (no real browser) -- that test lives in
`test_crew_trends_instagram_scraper.py`, split out purely for file-size
discipline (see that file's docstring for the technique).
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from typing import Any

import pytest
from crewai_tools import SerperDevTool

from lib.crew.trends.agent import build_trends_agent, build_trends_task
from lib.crew.trends.execute import _parse_structured_output
from lib.crew.trends.models import TrendSignal, TrendsOutput
from lib.crew.trends.prompts import build_trends_task_description

# ── TrendSignal / TrendsOutput models ────────────────────────────────────────


def _signal(**overrides: Any) -> TrendSignal:
    defaults: dict[str, Any] = {
        "keyword": "no-subscription gps tracker",
        "category": "GPS/Gear",
        "opportunity_type": "web_discovery",
        "score": 76.5,
        "reason": "Rising forum discussion found via live web search.",
    }
    defaults.update(overrides)
    return TrendSignal(**defaults)


def test_trend_signal_and_trends_output_round_trip() -> None:
    signal = _signal()
    output = TrendsOutput(signals=[signal])

    payload = json.loads(output.model_dump_json())
    parsed = TrendsOutput.model_validate(payload)

    assert parsed.signals == [signal]
    assert parsed.signals[0].keyword == "no-subscription gps tracker"
    assert parsed.signals[0].category == "GPS/Gear"
    assert parsed.signals[0].opportunity_type == "web_discovery"
    assert parsed.signals[0].score == 76.5
    assert parsed.signals[0].reason == "Rising forum discussion found via live web search."


def test_trends_output_defaults_to_empty_signals_list() -> None:
    assert TrendsOutput().signals == []
    assert TrendsOutput.model_validate({}).signals == []


# ── build_trends_task_description ────────────────────────────────────────────


def test_build_trends_task_description_includes_all_sections_without_instagram() -> None:
    description = build_trends_task_description(
        identity="Brand: DogFoodAndFun",
        seed_keywords="dog food, gps tracker",
        opportunities_json="[]",
        data_sufficient=False,
        instagram_trends_text=None,
    )
    assert "Brand: DogFoodAndFun" in description
    assert "dog food, gps tracker" in description
    assert "[]" in description
    assert "NOT yet sufficient" in description
    assert "(Instagram trends unavailable this run)" in description


def test_build_trends_task_description_sufficient_data_note() -> None:
    description = build_trends_task_description(
        identity="Brand: X",
        seed_keywords="",
        opportunities_json="[]",
        data_sufficient=True,
        instagram_trends_text=None,
    )
    assert "GSC history is sufficient" in description
    assert "(none on file)" in description


def test_build_trends_task_description_includes_instagram_text_when_present() -> None:
    instagram_text = "Trending: cozy autumn dog walk aesthetics, pumpkin treat recipes"
    description = build_trends_task_description(
        identity="Brand: DogFoodAndFun",
        seed_keywords="dog food",
        opportunities_json="[]",
        data_sufficient=True,
        instagram_trends_text=instagram_text,
    )
    assert instagram_text in description
    assert "(Instagram trends unavailable this run)" not in description
    assert "instagram_trend" in description


# ── build_trends_agent / build_trends_task ───────────────────────────────────


def test_build_trends_agent_has_serper_tool_wired() -> None:
    agent = build_trends_agent()
    assert agent.role == "Market Trend Scout"
    assert len(agent.tools) > 0
    assert any(isinstance(tool, SerperDevTool) for tool in agent.tools)


def test_build_trends_task_has_no_output_pydantic() -> None:
    """Guards against ever reintroducing `Task(output_pydantic=...)`, which is
    documented (see `lib.crew.trends.agent`'s module docstring) as broken
    against live DeepSeek in this crewai version."""
    agent = build_trends_agent()
    task = build_trends_task(agent, "some description")
    assert task.output_pydantic is None
    assert task.description == "some description"


# ── _parse_structured_output (pure JSON-parsing helper) ─────────────────────


def test_parse_structured_output_none_on_empty_input() -> None:
    assert _parse_structured_output(None) is None
    assert _parse_structured_output("") is None
    assert _parse_structured_output("   ") is None


def test_parse_structured_output_none_on_invalid_json() -> None:
    assert _parse_structured_output("not json at all") is None


def test_parse_structured_output_none_on_schema_mismatch() -> None:
    # valid JSON, but wrong shape -- "signals" must be a list of objects
    # matching TrendSignal, not a list of bare strings.
    assert _parse_structured_output(json.dumps({"signals": ["not-a-signal-object"]})) is None


def test_parse_structured_output_valid_json_round_trips() -> None:
    payload = {
        "signals": [
            {
                "keyword": "dog food",
                "category": "Recipes",
                "opportunity_type": "optimize",
                "score": 82.5,
                "reason": "carried forward from GSC opportunity",
            }
        ]
    }
    output = _parse_structured_output(json.dumps(payload))
    assert output is not None
    assert len(output.signals) == 1
    assert output.signals[0].keyword == "dog food"


def test_parse_structured_output_strips_code_fence_before_parsing() -> None:
    raw = "```json\n" + json.dumps({"signals": []}) + "\n```"
    output = _parse_structured_output(raw)
    assert output is not None
    assert output.signals == []


def test_parse_structured_output_extracts_json_from_surrounding_reasoning_text() -> None:
    """Regression test for a real DeepSeek run: ~30KB of visible
    chain-of-thought reasoning preceded the final JSON object, so the raw
    text didn't start with `{` and a direct `json.loads` on the whole
    string failed immediately with `Expecting value: line 1 column 1 (char
    0)`. `_parse_structured_output` must locate and parse the `{...}`
    object embedded in the reasoning text instead of giving up."""
    payload = {
        "signals": [
            {
                "keyword": "dog food",
                "category": "Recipes",
                "opportunity_type": "optimize",
                "score": 82.5,
                "reason": "carried forward from GSC opportunity",
            }
        ]
    }
    raw = (
        "Now I have gathered enough research across multiple search queries. "
        "Let me compile the final JSON.\n"
        + json.dumps(payload)
        + "\n\nWait, the output needs to be valid JSON matching the schema..."
    )
    output = _parse_structured_output(raw)
    assert output is not None
    assert len(output.signals) == 1
    assert output.signals[0].keyword == "dog food"


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
        "signals": [
            {
                "keyword": "draft keyword -- superseded",
                "category": "Recipes",
                "opportunity_type": "optimize",
                "score": 10.0,
                "reason": "this is the earlier draft, not the final answer",
            }
        ]
    }
    final_payload = {
        "signals": [
            {
                "keyword": "dog food",
                "category": "Recipes",
                "opportunity_type": "optimize",
                "score": 82.5,
                "reason": "carried forward from GSC opportunity",
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
    output = _parse_structured_output(raw)
    assert output is not None
    assert len(output.signals) == 1
    assert output.signals[0].keyword == "dog food"


def test_parse_structured_output_none_and_logs_when_no_json_object_anywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Genuinely malformed output (no `{...}` substring parses as valid
    JSON) must not be silently swallowed -- still logs
    `crew_trends_json_decode_failed` with the full raw text and returns
    `None`, exactly as before this fix."""
    import lib.crew.trends.execute as execute_module

    warnings: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        execute_module.logger,
        "warning",
        lambda event, **kwargs: warnings.append((event, kwargs)),
    )

    raw = "Let me think about this some more. I am not ready to answer yet."
    output = execute_module._parse_structured_output(raw)

    assert output is None
    assert len(warnings) == 1
    event, kwargs = warnings[0]
    assert event == "crew_trends_json_decode_failed"
    assert kwargs["raw_output"] == raw


# ── real (non-test) entry points -- importable/callable smoke tests only ────


def test_execute_trends_crew_is_importable_and_callable() -> None:
    """Only checked for importability/callability -- actually calling it
    would make a live CrewAI/DeepSeek/Serper network call, which this file
    never does (see module docstring)."""
    import lib.crew.trends.execute as execute_module

    assert callable(execute_module.execute_trends_crew)


def test_fetch_instagram_trends_text_is_importable_and_callable() -> None:
    """Only checked for importability/callability -- actually calling it
    would launch a real Playwright browser and hit instagram.com over the
    network, which this file never does (see module docstring)."""
    import lib.crew.trends.instagram_scraper as instagram_scraper_module

    assert callable(instagram_scraper_module.fetch_instagram_trends_text)
