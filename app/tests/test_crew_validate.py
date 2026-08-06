"""Tests for lib.crew.validate -- the two-gate validation step.

`editor_execute_fn` is always a plain injected callable (same convention as
`test_crew_writer_orchestrator.py`'s `execute_fn` injection) -- no real
CrewAI/DeepSeek network call, no DATABASE_URL. The medical-claims gate is the
real `lib.medical_claims_validator.validate_blog_post` (pure, no network),
exercised for real so the "hard-fail before the editor even runs" ordering
is actually proven, not just asserted.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path

import pytest
from crewai import Agent, Task

from lib.crew.editor.models import QualityVerdict
from lib.crew.validate import MIN_QUALITY_SCORE, validate_draft

# Modeled directly on the real, confirmed bug found in the GPS-tracker draft
# (app/brands/dogfoodandfun/state/crew_drafts/71f81336-....html): a visible,
# unfinished LLM self-correction leaking into the body text.
_REAL_BUG_EXCERPT = (
    "It has a GPS mode that uses cellular (but without a subscription, it only works "
    "when you're in Wi-Fi range? Actually, that's not accurate—let me clarify in the "
    "review below)."
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def brand_dir(tmp_path: Path) -> Path:
    _write(
        tmp_path / "config.json",
        json.dumps({"site": {"name": "DogFoodAndFun", "brand_persona": "Nalla's Dad"}}),
    )
    _write(
        tmp_path / "data" / "config" / "brand_voice_guide.md",
        "Data-driven, first-person, engineer-turned-dog-owner voice.",
    )
    return tmp_path


def _good_verdict(**overrides: object) -> QualityVerdict:
    defaults: dict[str, object] = {
        "score": 92.0,
        "passed": True,
        "issues": [],
        "stray_artifacts": [],
    }
    defaults.update(overrides)
    return QualityVerdict(**defaults)  # type: ignore[arg-type]


# ── medical-claims gate (hard-fail, runs first) ──────────────────────────────


def test_medical_claims_violation_rejects_before_editor_runs(brand_dir: Path) -> None:
    editor_called = False

    def fake_editor(agent: Agent, task: Task) -> QualityVerdict:
        nonlocal editor_called
        editor_called = True
        return _good_verdict()

    result = validate_draft(
        brand_dir,
        title="A Great Post",
        body_html="<p>As a veterinarian, I recommend this diet for every dog.</p>",
        editor_execute_fn=fake_editor,
    )

    assert result.passed is False
    assert result.quality_score is None
    assert any("medical_claims_validator" in reason for reason in result.reasons)
    assert editor_called is False


def test_medical_claims_gate_allows_clean_content_through(brand_dir: Path) -> None:
    result = validate_draft(
        brand_dir,
        title="A Great Post",
        body_html="<p>We tried this with our dog and it worked well for us.</p>",
        editor_execute_fn=lambda agent, task: _good_verdict(),
    )
    assert result.passed is True


# ── quality-editor gate: score threshold ─────────────────────────────────────


def test_low_score_rejected_even_with_no_stray_artifacts(brand_dir: Path) -> None:
    result = validate_draft(
        brand_dir,
        title="A Post",
        body_html="<p>Clean, on-topic content.</p>",
        editor_execute_fn=lambda agent, task: _good_verdict(score=55.0, stray_artifacts=[]),
    )
    assert result.passed is False
    assert result.quality_score == 55.0
    assert any(str(MIN_QUALITY_SCORE) in reason or "55.0" in reason for reason in result.reasons)


def test_score_at_exactly_threshold_passes(brand_dir: Path) -> None:
    result = validate_draft(
        brand_dir,
        title="A Post",
        body_html="<p>Clean, on-topic content.</p>",
        editor_execute_fn=lambda agent, task: _good_verdict(score=MIN_QUALITY_SCORE),
    )
    assert result.passed is True


# ── quality-editor gate: stray-artifact override (fails even on high score) ─


def test_high_score_still_rejected_when_stray_artifact_flagged(brand_dir: Path) -> None:
    """Regression coverage for the real GPS-tracker bug: a high rubric score
    must NOT be enough to pass if the editor flags a stray LLM artifact --
    the OR condition, not just the threshold, must gate."""
    result = validate_draft(
        brand_dir,
        title="GPS Trackers Without the Monthly Bill",
        body_html=f"<p>{_REAL_BUG_EXCERPT}</p>",
        editor_execute_fn=lambda agent, task: _good_verdict(
            score=95.0, stray_artifacts=[_REAL_BUG_EXCERPT]
        ),
    )
    assert result.passed is False
    assert result.quality_score == 95.0
    assert any(_REAL_BUG_EXCERPT in reason for reason in result.reasons)


def test_stray_artifact_reason_quotes_the_exact_excerpt(brand_dir: Path) -> None:
    result = validate_draft(
        brand_dir,
        title="t",
        body_html="<p>...</p>",
        editor_execute_fn=lambda agent, task: _good_verdict(
            score=99.0, stray_artifacts=["let me clarify that point further"]
        ),
    )
    assert result.passed is False
    assert any("let me clarify that point further" in reason for reason in result.reasons)


# ── fail-closed on missing/unparseable editor verdict ────────────────────────


def test_editor_returning_none_fails_closed(brand_dir: Path) -> None:
    result = validate_draft(
        brand_dir,
        title="t",
        body_html="<p>clean content</p>",
        editor_execute_fn=lambda agent, task: None,
    )
    assert result.passed is False
    assert result.quality_score is None
    assert result.reasons  # some reason must be logged, not a silent reject


# ── passing case ──────────────────────────────────────────────────────────────


def test_clean_high_score_passes_with_no_reasons(brand_dir: Path) -> None:
    result = validate_draft(
        brand_dir,
        title="A Great, Well-Structured Post",
        body_html="<p>Clean, coherent, on-brand content with no issues.</p>",
        editor_execute_fn=lambda agent, task: _good_verdict(score=88.0),
    )
    assert result.passed is True
    assert result.reasons == []
    assert result.quality_score == 88.0
