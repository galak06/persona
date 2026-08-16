"""Tests for lib.crew.editor -- quality-editor agent construction + output parsing.

No CrewAI kickoff, no network, no DATABASE_URL. `execute_editor_crew` is only
checked for importability/callability (same convention as
`test_crew_scout.py::test_execute_scout_crew_is_importable_and_callable`) --
actually calling it would make a live CrewAI/DeepSeek network call. The
JSON-parsing helpers it wraps (`_strip_code_fence`/`_parse_verdict`) are pure
and tested directly.
"""
# ruff: noqa: S101

from __future__ import annotations

import json

from lib.crew.editor.agent import build_editor_agent, build_editor_task
from lib.crew.editor.execute import _parse_verdict
from lib.crew.editor.models import QualityVerdict
from lib.crew.editor.prompts import build_editor_task_description

# `_strip_code_fence` was a private copy in `editor.execute`; collapsing the
# eight per-stage parsers left one implementation, public, in `json_recovery`.
from lib.crew.json_recovery import strip_code_fence as _strip_code_fence
from lib.crew.writer.models import OutlineSection

# ── QualityVerdict model ─────────────────────────────────────────────────────


def test_quality_verdict_defaults_to_empty_lists() -> None:
    verdict = QualityVerdict(score=90.0, passed=True)
    assert verdict.issues == []
    assert verdict.stray_artifacts == []


def test_quality_verdict_round_trips_full_payload() -> None:
    stray_artifact = "Actually, that's not accurate -- let me clarify below."
    payload = {
        "score": 42.5,
        "passed": False,
        "issues": ["voice drifts into generic marketing copy in section 3"],
        "stray_artifacts": [stray_artifact],
    }
    verdict = QualityVerdict.model_validate(payload)
    assert verdict.score == 42.5
    assert verdict.stray_artifacts == [stray_artifact]


# ── build_editor_task_description ───────────────────────────────────────────


def test_prompt_includes_title_body_voice_and_threshold() -> None:
    description = build_editor_task_description(
        title="Best No-Subscription GPS Trackers 2026",
        body_html="<p>Full post body here.</p>",
        voice="Data-driven, first-person engineer-turned-dog-owner.",
        outline=[],
        min_score=80.0,
    )
    assert "Best No-Subscription GPS Trackers 2026" in description
    assert "Full post body here." in description
    assert "engineer-turned-dog-owner" in description
    assert "80" in description


def test_prompt_flags_stray_artifact_hard_rule_with_real_bug_example() -> None:
    """The rubric text must explicitly instruct the model to catch the exact
    class of bug found in the real GPS-tracker draft (an unfinished
    self-correction: "Actually, that's not accurate -- let me clarify")."""
    description = build_editor_task_description(
        title="t", body_html="b", voice="v", outline=[], min_score=80.0
    )
    assert "stray_artifacts" in description
    assert "Actually, that's not accurate" in description
    assert "let me clarify" in description


def test_prompt_lists_outline_sections_when_provided() -> None:
    outline = [
        OutlineSection(heading="Hook", level="H2", notes="mascot story opener"),
        OutlineSection(heading="FAQ", level="H2", notes="answer brief questions"),
    ]
    description = build_editor_task_description(
        title="t", body_html="b", voice="v", outline=outline, min_score=80.0
    )
    assert "Hook" in description
    assert "mascot story opener" in description


def test_prompt_falls_back_to_standard_blueprint_when_no_outline() -> None:
    description = build_editor_task_description(
        title="t", body_html="b", voice="v", outline=[], min_score=80.0
    )
    assert "standard blueprint" in description


# ── build_editor_agent / build_editor_task ───────────────────────────────────


def test_build_editor_agent_has_no_tools_and_deepseek_model() -> None:
    agent = build_editor_agent()
    assert agent.role == "Quality Editor"
    assert agent.tools == []
    # CrewAI parses the "deepseek/deepseek-chat" llm= string into its own LLM
    # object -- assert on the parsed model name rather than the raw string.
    assert agent.llm is not None and not isinstance(agent.llm, str)
    assert "deepseek-chat" in str(agent.llm.model)


def test_build_editor_task_expected_output_embeds_schema_fields() -> None:
    agent = build_editor_agent()
    task = build_editor_task(agent, "some description")
    assert "score" in task.expected_output
    assert "stray_artifacts" in task.expected_output
    assert task.description == "some description"


# ── _strip_code_fence / _parse_verdict (pure JSON-parsing helpers) ──────────


def test_strip_code_fence_removes_json_fence() -> None:
    fenced = '```json\n{"score": 90}\n```'
    assert _strip_code_fence(fenced) == '{"score": 90}'


def test_strip_code_fence_passthrough_when_no_fence() -> None:
    assert _strip_code_fence('{"score": 90}') == '{"score": 90}'


def test_parse_verdict_valid_json() -> None:
    raw = json.dumps({"score": 88.0, "passed": True, "issues": [], "stray_artifacts": []})
    verdict = _parse_verdict(raw)
    assert verdict is not None
    assert verdict.score == 88.0
    assert verdict.passed is True


def test_parse_verdict_strips_fence_before_parsing() -> None:
    raw = "```json\n" + json.dumps({"score": 50.0, "passed": False}) + "\n```"
    verdict = _parse_verdict(raw)
    assert verdict is not None
    assert verdict.score == 50.0


def test_parse_verdict_none_on_empty_input() -> None:
    assert _parse_verdict(None) is None
    assert _parse_verdict("") is None
    assert _parse_verdict("   ") is None


def test_parse_verdict_none_on_invalid_json() -> None:
    assert _parse_verdict("not json at all") is None


def test_parse_verdict_none_on_schema_mismatch() -> None:
    # missing required "score"/"passed" fields
    assert _parse_verdict(json.dumps({"issues": ["x"]})) is None


def test_execute_editor_crew_is_importable_and_callable() -> None:
    """Only checked for importability/callability -- actually calling it
    would make a live CrewAI/DeepSeek network call, which this file never
    does (see module docstring)."""
    import lib.crew.editor.execute as execute_module

    assert callable(execute_module.execute_editor_crew)
