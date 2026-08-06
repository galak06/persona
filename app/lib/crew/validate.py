"""Validation gate for the CrewAI content pipeline -- the substitute for
human review before a draft reaches WordPress.

Runs two hard-fail checks, in order, on the fully-assembled post (title +
body_html):

  1. `lib.medical_claims_validator.validate_blog_post` -- deterministic,
     regex-based, catches `ValueError` and treats it as an immediate reject.
     The quality editor never runs if this fails first (cheaper, and a
     compliance failure doesn't need a second opinion).
  2. The `lib.crew.editor` quality-editor agent -- rejects if the score is
     below `MIN_QUALITY_SCORE` OR if it flags any stray-LLM-artifact issue;
     both conditions are reported independently when they fire.

Both checks fail closed: a missing/unparseable editor verdict is treated as
a reject, not a pass-through (`lib.crew.editor.execute.execute_editor_crew`
already returns `None` on any LLM/parse failure -- this module refuses to
draft on `None` rather than assume the post is fine).

`MIN_QUALITY_SCORE = 80.0` mirrors this repo's existing "auto-approve"
relevance-scoring tier (`app/CLAUDE.md`'s engagement scoring: "Auto-approve:
>= 0.80"). That tier was originally paired with a human still reading
borderline (0.75-0.80) cases; this pipeline has NO human in the loop at all
(explicit, repeated decision -- see the plan doc), so there is no fallback
for a borderline draft to land in. Adopting the strict "no human needed" bar
rather than the looser "flag for review" bar is intentional: a rejected
draft simply doesn't get created (cheap, logged, re-run next time), while an
under-scrutinized one reaching a live site's draft list is the actual risk
this gate exists to prevent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from crewai import Agent, Task

from lib.crew.context import brand_voice_summary
from lib.crew.editor.agent import build_editor_agent, build_editor_task
from lib.crew.editor.execute import execute_editor_crew
from lib.crew.editor.models import QualityVerdict
from lib.crew.editor.prompts import build_editor_task_description
from lib.crew.writer.models import OutlineSection
from lib.medical_claims_validator import validate_blog_post
from lib.observability import get_logger

logger = get_logger(__name__)

MIN_QUALITY_SCORE = 80.0

EditorExecuteFn = Callable[[Agent, Task], QualityVerdict | None]


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of `validate_draft`. `quality_score` is only `None` when the
    medical-claims gate rejected first (the editor never ran) or the editor
    produced no parseable verdict -- in every other case it carries the
    real 0-100 score regardless of pass/fail, so a rejection is debuggable."""

    passed: bool
    reasons: list[str] = field(default_factory=list)
    quality_score: float | None = None


def validate_draft(
    brand_dir: Path,
    *,
    title: str,
    body_html: str,
    outline: list[OutlineSection] | None = None,
    editor_execute_fn: EditorExecuteFn | None = None,
) -> ValidationResult:
    """Run both hard-fail gates in order on one assembled post.

    Never raises -- a validator exception would itself be a fail-open bug
    in a gate whose entire purpose is failing closed.
    """
    try:
        validate_blog_post(body_html, title=title)
    except ValueError as exc:
        logger.warning("crew_validate_medical_claims_rejected", reason=str(exc))
        return ValidationResult(passed=False, reasons=[f"medical_claims_validator: {exc}"])

    voice = brand_voice_summary(brand_dir)
    description = build_editor_task_description(
        title=title,
        body_html=body_html,
        voice=voice,
        outline=outline or [],
        min_score=MIN_QUALITY_SCORE,
    )
    agent = build_editor_agent()
    task = build_editor_task(agent, description)
    execute_fn = editor_execute_fn or execute_editor_crew
    verdict = execute_fn(agent, task)
    if verdict is None:
        logger.warning("crew_validate_editor_produced_no_verdict")
        return ValidationResult(
            passed=False,
            reasons=[
                "quality-editor agent produced no structured verdict "
                "(LLM/parse failure) -- failing closed"
            ],
        )

    reasons: list[str] = []
    if verdict.score < MIN_QUALITY_SCORE:
        reasons.append(
            f"quality score {verdict.score:.1f} is below the {MIN_QUALITY_SCORE:.1f} threshold"
        )
    if verdict.stray_artifacts:
        reasons.extend(
            f"stray LLM artifact detected: {excerpt!r}" for excerpt in verdict.stray_artifacts
        )
    passed = not reasons
    if not passed:
        reasons.extend(verdict.issues)
        logger.warning("crew_validate_editor_rejected", score=verdict.score, reasons=reasons)
    else:
        logger.info("crew_validate_passed", score=verdict.score)
    return ValidationResult(passed=passed, reasons=reasons, quality_score=verdict.score)
