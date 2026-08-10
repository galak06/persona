"""Real `Crew(...).kickoff()` wrapper for the Social Post Writer stage.

Same shape as `lib.crew.reels.execute.execute_reels_crew`: the only function
in this package that makes a real LLM/network call, returning `None` (logged,
not raised) on any failure so the orchestrator never crashes the run.

Adds one retry-with-feedback pass when the parsed plan violates the hard
caption rules (`lib.crew.socialpost.rules`) -- same shape as the reels crew's
beat-count retry, because these are structural requirements (URL placement,
hashtag counts, keyword-in-first-sentence), not nice-to-haves.

`_strip_code_fence`/`_iter_balanced_json_object_candidates` are a local,
independent copy -- deliberately NOT imported from `lib.crew.reels.execute` or
any sibling -- matching this repo's "independently-evolving pipelines"
convention.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from crewai import Agent, Task
from pydantic import BaseModel, ValidationError

from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.socialpost.rules import find_caption_violations
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


def _kickoff_and_parse(agent: Agent, task: Task) -> SocialPostPlan | None:
    from crewai import Crew  # local import: keeps Crew construction next to its one use

    try:
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        crew.kickoff()
    except Exception as exc:  # CrewAI/LiteLLM/network errors
        logger.warning("crew_socialpost_kickoff_failed", error=str(exc))
        return None

    raw = task.output.raw if task.output else None
    return _parse_structured_output(raw, SocialPostPlan, event="crew_socialpost")


def execute_social_post_crew(
    agent: Agent, task: Task, *, target_keyword: str
) -> SocialPostPlan | None:
    """Run the real Social Post `Crew(...).kickoff()`, with one retry-with-
    feedback pass if the parsed plan breaks the hard caption rules. `None` on
    any failure, including a retry that still violates them."""
    plan = _kickoff_and_parse(agent, task)
    if plan is None:
        return None
    violations = find_caption_violations(plan, target_keyword=target_keyword)
    if not violations:
        return plan

    logger.warning("crew_socialpost_rule_violations_retrying", violations=violations)
    bullet_list = "\n".join(f"- {v}" for v in violations)
    task.description = (
        task.description
        + "\n\n---\nPREVIOUS DRAFT WAS REJECTED for breaking these hard rules:\n"
        + bullet_list
        + "\nRe-draft the full plan fixing every one of them. All other rules still apply."
    )
    retry_plan = _kickoff_and_parse(agent, task)
    if retry_plan is None:
        return None
    retry_violations = find_caption_violations(retry_plan, target_keyword=target_keyword)
    if retry_violations:
        logger.warning("crew_socialpost_rule_violations_after_retry", violations=retry_violations)
        return None
    return retry_plan
