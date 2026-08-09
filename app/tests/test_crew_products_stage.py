"""Tests for the lib.crew.products LLM stage -- models, prompt building,
agent/task construction, and the manual-JSON-parsing execute path.

Same convention as test_crew_writer_orchestrator.py / test_crew_reels.py:
the CrewAI `kickoff()` call is never exercised for real. The pure JSON
helpers are tested directly, and `execute_product_selector_crew`'s local
`from crewai import Crew` is covered by installing a fake `crewai` module in
`sys.modules` (patching `lib.crew.products.execute.Crew` wouldn't work --
the name is never bound at module level). No real CrewAI/DeepSeek network
calls, no DATABASE_URL.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from lib.crew.products.agent import (
    DEEPSEEK_MODEL,
    build_product_selector_agent,
    build_product_selector_task,
)
from lib.crew.products.execute import _parse_selection, execute_product_selector_crew
from lib.crew.products.models import ProductSelection, SelectedProduct
from lib.crew.products.prompts import build_selector_task_description
from lib.crew.writer.models import ContentBrief, OutlineSection

_CANDIDATES_TEXT = (
    "gps_tracker | PawTrack GPS Collar | gear | no-subscription tracker\n"
    "run_harness | TrailBlazer Harness | gear | secure fit for running dogs\n"
    "treat_pouch | SnackPack Pouch | accessories | hands-free treat carry"
)


def _brief(**overrides: Any) -> ContentBrief:
    defaults: dict[str, Any] = {
        "suggested_title": "Best No-Subscription GPS Trackers 2026",
        "primary_keyword": "no subscription dog gps tracker",
        "secondary_keywords": ["gps collar without monthly fee", "offline dog tracker"],
        "outline": [
            OutlineSection(heading="Why Subscriptions Add Up", notes="Cost math over 3 years."),
            OutlineSection(heading="Top Picks Compared", level="H3", notes="Range, battery."),
        ],
        "mascot_angle": "Nalla wandered off during a trail run once.",
    }
    defaults.update(overrides)
    return ContentBrief(**defaults)


def _selection_json(products: list[dict[str, str]]) -> str:
    return json.dumps({"products": products})


_GOOD_PRODUCTS = [
    {"key": "gps_tracker", "reason": "Directly answers the post's no-subscription question."},
    {"key": "run_harness", "reason": "Trail-running readers need a secure harness."},
]


# ── models ───────────────────────────────────────────────────────────────────


def test_selected_product_requires_key_and_reason() -> None:
    product = SelectedProduct(key="gps_tracker", reason="Fits the post's topic directly.")
    assert product.key == "gps_tracker"
    with pytest.raises(ValidationError):
        SelectedProduct(key="gps_tracker")  # type: ignore[call-arg]


def test_product_selection_defaults_to_empty_list() -> None:
    assert ProductSelection().products == []


def test_product_selection_with_empty_products_list_is_valid() -> None:
    selection = ProductSelection.model_validate({"products": []})
    assert selection.products == []


# ── build_selector_task_description ──────────────────────────────────────────


def test_prompt_contains_post_plan_and_candidates() -> None:
    description = build_selector_task_description(
        brief=_brief(), candidates_text=_CANDIDATES_TEXT, max_products=3
    )
    assert "Best No-Subscription GPS Trackers 2026" in description
    assert "no subscription dog gps tracker" in description
    assert "gps collar without monthly fee" in description
    assert "Why Subscriptions Add Up" in description
    assert "PawTrack GPS Collar" in description
    assert "AT MOST 3" in description


def test_prompt_explicitly_permits_an_empty_selection() -> None:
    description = build_selector_task_description(
        brief=_brief(), candidates_text=_CANDIDATES_TEXT, max_products=3
    )
    assert "empty products list) is a valid, correct answer" in description


def test_prompt_forbids_invented_keys_and_prices() -> None:
    description = build_selector_task_description(
        brief=_brief(), candidates_text=_CANDIDATES_TEXT, max_products=3
    )
    assert "NEVER invent a key" in description
    assert "Never mention prices." in description


def test_prompt_embeds_product_selection_schema() -> None:
    description = build_selector_task_description(
        brief=_brief(), candidates_text=_CANDIDATES_TEXT, max_products=3
    )
    assert json.dumps(ProductSelection.model_json_schema()) in description


# ── agent/task construction ──────────────────────────────────────────────────


def test_build_product_selector_agent_defaults_to_deepseek() -> None:
    agent = build_product_selector_agent()
    assert DEEPSEEK_MODEL == "deepseek/deepseek-chat"
    assert agent.role == "Affiliate Product Curator"
    assert agent.tools == []


def test_build_product_selector_task_embeds_schema_in_expected_output() -> None:
    agent = build_product_selector_agent()
    task = build_product_selector_task(agent, "the description")
    assert task.description == "the description"
    assert json.dumps(ProductSelection.model_json_schema()) in task.expected_output


# ── _parse_selection ─────────────────────────────────────────────────────────


def test_parse_selection_accepts_good_payload() -> None:
    selection = _parse_selection(_selection_json(_GOOD_PRODUCTS))
    assert selection is not None
    assert [p.key for p in selection.products] == ["gps_tracker", "run_harness"]


def test_parse_selection_accepts_empty_products_list_as_valid() -> None:
    selection = _parse_selection(_selection_json([]))
    assert selection is not None
    assert selection.products == []


def test_parse_selection_strips_code_fence() -> None:
    fenced = f"```json\n{_selection_json(_GOOD_PRODUCTS)}\n```"
    selection = _parse_selection(fenced)
    assert selection is not None
    assert len(selection.products) == 2


def test_parse_selection_malformed_json_returns_none() -> None:
    assert _parse_selection('{"products": [truncated') is None


def test_parse_selection_schema_mismatch_returns_none() -> None:
    assert _parse_selection(json.dumps({"products": [{"key": "gps_tracker"}]})) is None


def test_parse_selection_empty_output_returns_none() -> None:
    assert _parse_selection(None) is None
    assert _parse_selection("   ") is None


# ── execute_product_selector_crew ────────────────────────────────────────────


def test_execute_parses_good_payload_from_task_output() -> None:
    agent = build_product_selector_agent()
    task = build_product_selector_task(agent, "desc")
    task.output = MagicMock(raw=_selection_json(_GOOD_PRODUCTS))

    fake_crewai = MagicMock()
    fake_crewai.Crew.return_value.kickoff.return_value = None
    with patch.dict("sys.modules", {"crewai": fake_crewai}):
        selection = execute_product_selector_crew(agent, task)

    assert selection is not None
    assert [p.key for p in selection.products] == ["gps_tracker", "run_harness"]


def test_execute_returns_none_on_malformed_json_output() -> None:
    agent = build_product_selector_agent()
    task = build_product_selector_task(agent, "desc")
    task.output = MagicMock(raw="not json at all")

    fake_crewai = MagicMock()
    fake_crewai.Crew.return_value.kickoff.return_value = None
    with patch.dict("sys.modules", {"crewai": fake_crewai}):
        assert execute_product_selector_crew(agent, task) is None


def test_execute_returns_none_when_kickoff_raises() -> None:
    agent = build_product_selector_agent()
    task = build_product_selector_task(agent, "desc")

    fake_crewai = MagicMock()
    fake_crewai.Crew.return_value.kickoff.side_effect = RuntimeError("LLM down")
    with patch.dict("sys.modules", {"crewai": fake_crewai}):
        assert execute_product_selector_crew(agent, task) is None
