"""Tests for lib.crew.reels -- fallback Reel Beats agent construction, prompt
building, output parsing, and the beat-count retry orchestration.

Same convention as `test_crew_idea.py`: `execute_reels_crew` is only
covered via a mocked `Crew.kickoff` for the retry-logic branch (real
behavior worth covering, unlike a thin network wrapper) -- never a live
CrewAI/DeepSeek call. The JSON-parsing helpers are pure and tested directly.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from lib.crew.reels.agent import build_reels_agent, build_reels_task
from lib.crew.reels.execute import _parse_structured_output, execute_reels_crew
from lib.crew.reels.models import REEL_BEAT_COUNT, ReelBeat, ReelPlan
from lib.crew.reels.prompts import build_reels_task_description


def _plan_json(n_beats: int) -> str:
    return json.dumps(
        {
            "beats": [
                {"headline": f"H{i}", "subcopy": f"S{i}", "image_prompt": f"P{i}"}
                for i in range(n_beats)
            ],
            "ig_caption": "IG caption",
            "fb_caption": "FB caption",
        }
    )


# ── models ────────────────────────────────────────────────────────────────


def test_reel_beat_count_constant_is_five() -> None:
    assert REEL_BEAT_COUNT == 5


def test_reel_plan_accepts_beats_list() -> None:
    plan = ReelPlan(
        beats=[ReelBeat(headline="H", subcopy="S", image_prompt="P")],
        ig_caption="ig",
        fb_caption="fb",
    )
    assert len(plan.beats) == 1


# ── build_reels_task_description ─────────────────────────────────────────


def test_build_reels_task_description_includes_all_sections() -> None:
    description = build_reels_task_description(
        title="5 GPS Trackers We Tested",
        body="Full post body text here.",
        brand_voice="Warm, honest, data-driven.",
    )
    assert "5 GPS Trackers We Tested" in description
    assert "Full post body text here." in description
    assert "Warm, honest, data-driven." in description
    assert "exactly 5 beats" in description or "5 beats" in description


def test_build_reels_task_description_missing_voice_fallback() -> None:
    description = build_reels_task_description(title="T", body="B", brand_voice="")
    assert "(no voice guide available)" in description


# ── reference_category (optional reference-photo library) ────────────────


def test_reel_beat_parses_payload_without_reference_category() -> None:
    """Back-compat: plans are re-parsed from LLM JSON every run, and a model
    that simply omits the new field must still validate."""
    output = _parse_structured_output(_plan_json(REEL_BEAT_COUNT), ReelPlan, event="test_event")
    assert output is not None
    assert all(beat.reference_category == "" for beat in output.beats)


def test_reel_beat_keeps_reference_category_when_the_model_sets_one() -> None:
    beat = ReelBeat(headline="H", subcopy="S", image_prompt="P", reference_category="Eating")
    assert beat.reference_category == "Eating"


def test_build_reels_task_description_omits_reference_section_without_a_library() -> None:
    """A brand with no reference-photo collections gets the prompt it always
    got -- the section is not rendered at all, not rendered empty."""
    description = build_reels_task_description(title="T", body="B", brand_voice="V")
    assert "reference_category" not in description
    assert "Reference-photo collection" not in description


def test_build_reels_task_description_lists_reference_categories_when_given() -> None:
    description = build_reels_task_description(
        title="T",
        body="B",
        brand_voice="V",
        reference_categories=("Eating", "Outdoors"),
    )
    assert "## Reference-photo collection (`reference_category`)" in description
    assert "- Eating" in description
    assert "- Outdoors" in description
    # The per-beat instruction and the no-clean-match rule are both spelled out.
    assert "For each beat" in description
    assert "closest" in description.lower()
    assert "copied verbatim from the list above" in description


def test_build_reels_task_description_never_offers_an_unlisted_catch_all() -> None:
    """The live bug: the section closed with *if none of them fits, use
    "general"* -- naming a collection that was not in the list it had just
    supplied. The planner took that exit on 2 of 5 beats and both resolved to
    no image at all."""
    description = build_reels_task_description(
        title="T",
        body="B",
        brand_voice="V",
        reference_categories=("forest-trail", "home-exterior", "studio-mascot"),
    )
    assert "general" not in description.lower()


def test_build_reels_task_description_offers_a_catch_all_the_brand_actually_has() -> None:
    """A brand that keeps a stocked catch-all still gets it offered -- and by
    the exact label it was handed, because the model is told to copy verbatim."""
    description = build_reels_task_description(
        title="T",
        body="B",
        brand_voice="V",
        reference_categories=("forest-trail", "General"),
    )
    assert '"General"' in description


# ── build_reels_agent / build_reels_task ─────────────────────────────────


def test_build_reels_agent_has_no_tools() -> None:
    agent = build_reels_agent()
    assert agent.role == "Reel Beats Writer"
    assert agent.tools == []


def test_build_reels_task_has_no_output_pydantic() -> None:
    agent = build_reels_agent()
    task = build_reels_task(agent, "some description")
    assert task.output_pydantic is None
    assert task.description == "some description"


# ── _parse_structured_output (pure JSON-parsing helper) ─────────────────


def test_parse_structured_output_none_on_empty_input() -> None:
    assert _parse_structured_output(None, ReelPlan, event="test_event") is None
    assert _parse_structured_output("", ReelPlan, event="test_event") is None


def test_parse_structured_output_none_on_invalid_json() -> None:
    assert _parse_structured_output("not json", ReelPlan, event="test_event") is None


def test_parse_structured_output_valid_json_round_trips() -> None:
    output = _parse_structured_output(_plan_json(5), ReelPlan, event="test_event")
    assert output is not None
    assert len(output.beats) == 5
    assert output.ig_caption == "IG caption"


def test_parse_structured_output_extracts_json_from_surrounding_reasoning_text() -> None:
    raw = "Some chain-of-thought reasoning first.\n" + _plan_json(5) + "\nTrailing notes."
    output = _parse_structured_output(raw, ReelPlan, event="test_event")
    assert output is not None
    assert len(output.beats) == 5


# ── execute_reels_crew: beat-count retry orchestration ───────────────────


def test_execute_reels_crew_returns_plan_when_first_attempt_has_five_beats() -> None:
    agent = build_reels_agent()
    task = build_reels_task(agent, "desc")
    task.output = MagicMock(raw=_plan_json(5))

    fake_crewai = MagicMock()
    fake_crewai.Crew.return_value.kickoff.return_value = None
    with patch.dict("sys.modules", {"crewai": fake_crewai}):
        plan = execute_reels_crew(agent, task)

    assert plan is not None
    assert len(plan.beats) == 5


def test_execute_reels_crew_retries_once_on_wrong_beat_count_and_succeeds() -> None:
    """`_kickoff_and_parse` does a *local* `from crewai import Crew`, so the
    fake module must be installed in `sys.modules` before `execute_reels_crew`
    runs -- patching `lib.crew.reels.execute.Crew` wouldn't work here, since
    that name is never bound at module level."""
    agent = build_reels_agent()
    task = build_reels_task(agent, "original description")
    original_description = task.description

    # First kickoff call returns a 3-beat plan (wrong); second (retry) returns 5.
    outputs = [MagicMock(raw=_plan_json(3)), MagicMock(raw=_plan_json(5))]

    def _fake_kickoff() -> None:
        task.output = outputs.pop(0)

    fake_crewai = MagicMock()
    fake_crewai.Crew.return_value.kickoff.side_effect = _fake_kickoff
    with patch.dict("sys.modules", {"crewai": fake_crewai}):
        plan = execute_reels_crew(agent, task)

    assert plan is not None
    assert len(plan.beats) == 5
    assert fake_crewai.Crew.return_value.kickoff.call_count == 2
    # The retry must have appended feedback to the task description.
    assert task.description != original_description
    assert "REJECTED" in task.description


def test_execute_reels_crew_none_when_retry_also_has_wrong_beat_count() -> None:
    agent = build_reels_agent()
    task = build_reels_task(agent, "desc")
    outputs = [MagicMock(raw=_plan_json(3)), MagicMock(raw=_plan_json(4))]

    def _fake_kickoff() -> None:
        task.output = outputs.pop(0)

    fake_crewai = MagicMock()
    fake_crewai.Crew.return_value.kickoff.side_effect = _fake_kickoff
    with patch.dict("sys.modules", {"crewai": fake_crewai}):
        plan = execute_reels_crew(agent, task)

    assert plan is None
