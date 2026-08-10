"""Real `Crew(...).kickoff()` wrapper for the fallback Reel Beats stage.

Same shape as `lib.crew.idea.execute.execute_idea_crew`: the only function
in this path that makes a real LLM/network call, returning `None` (logged,
not raised) on any failure so the orchestrator's fallback never crashes the
run.

Unlike `execute_idea_crew`, this adds one retry-with-feedback pass on a
`len(beats) != REEL_BEAT_COUNT` violation -- same shape as
`recipe-publisher/generators/carousel_drafter.py::draft_carousel`'s existing
retry-on-hard-limit-violation, since beat count here is a hard structural
requirement (see `lib.crew.reels.models.ReelPlan`), not just a nice-to-have.

`_strip_code_fence`/`_extract_json_payload` are a local, independent copy --
deliberately NOT imported from `lib.crew.idea.execute` or any sibling --
matching this repo's "independently-evolving pipelines" convention.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from crewai import Agent, Task
from pydantic import BaseModel, ValidationError

from lib.crew.reels.models import REEL_BEAT_COUNT, ReelPlan
from lib.observability import get_logger

logger = get_logger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    """Strip a wrapping ` ```json ... ``` ` fence, if present."""
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match else text.strip()


def _iter_balanced_json_object_candidates(text: str) -> list[str]:
    """Return every top-level, brace-balanced `{...}` substring found in
    `text`, in the order they appear. Same brace-depth/string-skipping
    algorithm as `lib.crew.idea.execute`'s copy -- see that module's
    docstring for the reasoning-model-prose rationale."""
    candidates: list[str] = []
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : i + 1])
                start = None
    return candidates


def _extract_json_payload(text: str) -> object | None:
    """Find and parse the most likely intended JSON object embedded in
    `text`, trying candidates last-to-first (the model's actual final
    answer). `None` if no candidate parses."""
    for candidate in reversed(_iter_balanced_json_object_candidates(text)):
        try:
            parsed: object = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return parsed
    return None


def _parse_structured_output(raw: str | None, model: type[ModelT], *, event: str) -> ModelT | None:
    """Parse+validate one CrewAI task's raw text output against `model`.
    `None` (logged) on missing output, invalid JSON, or a schema mismatch."""
    if not raw or not raw.strip():
        logger.warning(f"{event}_empty_output")
        return None
    text = _strip_code_fence(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        payload = _extract_json_payload(text)
        if payload is None:
            logger.warning(f"{event}_json_decode_failed", error=str(exc), raw_output=text)
            return None
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        logger.warning(f"{event}_schema_validation_failed", error=str(exc))
        return None


def _kickoff_and_parse(agent: Agent, task: Task) -> ReelPlan | None:
    from crewai import Crew  # local import: keeps Crew construction next to its one use

    try:
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        crew.kickoff()
    except Exception as exc:  # CrewAI/LiteLLM/network errors
        logger.warning("crew_reels_kickoff_failed", error=str(exc))
        return None

    raw = task.output.raw if task.output else None
    return _parse_structured_output(raw, ReelPlan, event="crew_reels")


def execute_reels_crew(agent: Agent, task: Task) -> ReelPlan | None:
    """Run the real Reel Beats `Crew(...).kickoff()`, with one retry-with-
    feedback pass if the parsed plan doesn't have exactly
    `REEL_BEAT_COUNT` beats. `None` on any failure, including a retry that
    still violates the beat count."""
    plan = _kickoff_and_parse(agent, task)
    if plan is None:
        return None
    if len(plan.beats) == REEL_BEAT_COUNT:
        return plan

    logger.warning(
        "crew_reels_beat_count_violation_retrying",
        got=len(plan.beats),
        expected=REEL_BEAT_COUNT,
    )
    task.description = (
        task.description
        + f"\n\n---\nPREVIOUS DRAFT WAS REJECTED: it had {len(plan.beats)} beats, "
        f"not exactly {REEL_BEAT_COUNT}. Re-draft with EXACTLY {REEL_BEAT_COUNT} beats: "
        "hook, point, point, point, CTA -- no more, no fewer."
    )
    retry_plan = _kickoff_and_parse(agent, task)
    if retry_plan is None or len(retry_plan.beats) != REEL_BEAT_COUNT:
        logger.warning("crew_reels_beat_count_violation_after_retry")
        return None
    return retry_plan
