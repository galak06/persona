"""Pydantic output contract for the CrewAI quality-editor agent.

One `Task` target (see `lib.crew.editor.agent`): `QualityVerdict` -- the
editor's structured rubric score plus a debuggable list of specific issues.
Same rationale as `lib.crew.writer.models.ContentBrief`/`WrittenPost`:
CrewAI's `output_pydantic` requires a `BaseModel`.

`lib.crew.validate.validate_draft` is the sole consumer -- it applies the
actual pass/fail threshold (`MIN_QUALITY_SCORE`) and the "any stray artifact
found is an automatic reject" override; this model only carries the editor's
raw assessment.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QualityVerdict(BaseModel):
    """The quality-editor agent's structured assessment of one assembled post."""

    score: float = Field(
        description="Overall quality score, 0-100, per the rubric in the task prompt "
        "(coherence, on-brand voice, structural completeness, absence of stray artifacts)."
    )
    passed: bool = Field(
        description="The editor's own pass/fail self-assessment. Informational only -- "
        "lib.crew.validate.validate_draft recomputes the real gate decision from `score` "
        "and `stray_artifacts` rather than trusting this field blindly."
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Specific, concrete rubric issues found (coherence/voice/structure "
        "problems) -- empty if none. Each entry should quote or closely paraphrase the "
        "actual problem, not a generic category name.",
    )
    stray_artifacts: list[str] = Field(
        default_factory=list,
        description="Verbatim excerpts of any stray LLM artifact found: an unfinished "
        "self-correction, meta-commentary about the writing process, a 'let me clarify' "
        "aside, or any text that reads like the model thinking out loud rather than "
        "finished prose. Empty if none found. ANY non-empty entry here is an automatic "
        "reject regardless of `score`.",
    )
