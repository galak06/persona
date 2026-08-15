"""Real `Crew(...).kickoff()` wrapper for the Social Post Writer stage.

Same shape as `lib.crew.reels.execute.execute_reels_crew`: the only function
in this package that makes a real LLM/network call, returning `None` (logged,
not raised) on any failure so the orchestrator never crashes the run.

Retries with feedback when the parsed plan violates the hard caption rules
(`lib.crew.socialpost.rules`) -- same shape as the reels crew's beat-count
retry, because these are structural requirements (no URLs, hashtag counts,
comment-keyword CTA), not nice-to-haves.

Two things learned from live runs, both encoded below:

1. **Retries must be surgical, not a re-draft.** Asking for a fresh plan made
   the model fix the named rule and break a different one -- observed twice on
   the same idea: attempt 1 missed the closing question, attempt 2 added it and
   simultaneously grew FB hashtags and a 6th IG hashtag. The retry prompt now
   says to return the SAME plan with only the listed problems corrected.
2. **One retry is not enough.** Each pass does fix what it was told about, so
   the ceiling is `MAX_ATTEMPTS`, not one -- with surgical retries the drift
   that made extra attempts pointless is gone.

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

# Total kickoffs allowed per idea: the first draft plus two corrections. Each
# correction is one DeepSeek call (~7s), so the worst case stays well inside
# the orchestrator's per-idea budget.
MAX_ATTEMPTS = 3

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


class KickoffError(Exception):
    """The Crew call itself failed -- network, auth, LiteLLM, quota.

    Distinct from "the model answered, but the answer was unusable". Retrying
    the former just multiplies the wait against a failure that will not fix
    itself (a revoked API key retried three times is three 401s and triple the
    time-to-diagnose); retrying the latter is the whole point, since malformed
    or schema-short output is non-deterministic drift.
    """


def _kickoff_and_parse(agent: Agent, task: Task) -> SocialPostPlan | None:
    """`None` when the model answered unusably (retryable by the caller).

    Raises `KickoffError` when the call never produced an answer at all.
    """
    from crewai import Crew  # local import: keeps Crew construction next to its one use

    try:
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        crew.kickoff()
    except Exception as exc:  # CrewAI/LiteLLM/network errors
        logger.warning("crew_socialpost_kickoff_failed", error=str(exc))
        raise KickoffError(str(exc)) from exc

    raw = task.output.raw if task.output else None
    return _parse_structured_output(raw, SocialPostPlan, event="crew_socialpost")


def _correction_prompt(plan: SocialPostPlan, violations: list[str]) -> str:
    """Feedback for one retry: hand back the rejected draft and ask for a
    minimal correction.

    Handing the draft back verbatim is the point. Without it the model writes
    a fresh plan from the original brief and re-breaks rules it had already
    satisfied; with it, the task is an edit rather than a rewrite.
    """
    bullets = "\n".join(f"- {v}" for v in violations)
    return (
        "\n\n---\nYOUR PREVIOUS DRAFT WAS REJECTED. It was correct except for the "
        "problems listed below.\n\nPrevious draft:\n"
        f"{plan.model_dump_json(indent=2)}\n\n"
        f"Problems to fix:\n{bullets}\n\n"
        "Return the SAME plan with ONLY those problems corrected. Keep every other "
        "field and sentence exactly as it was -- do not rewrite the captions, do not "
        "change the hashtags unless a problem above is about hashtags, and do not "
        "introduce any new rule violation while fixing these."
    )


def execute_social_post_crew(
    agent: Agent, task: Task, *, target_keyword: str
) -> SocialPostPlan | None:
    """Run the real Social Post `Crew(...).kickoff()`, retrying with surgical
    feedback while the plan breaks hard caption rules. `None` if no attempt
    within `MAX_ATTEMPTS` produces a clean plan."""
    base_description = task.description
    plan: SocialPostPlan | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            plan = _kickoff_and_parse(agent, task)
        except KickoffError:
            # The call never landed. Already logged with its cause; retrying
            # would only multiply the wait against the same failure.
            return None
        if plan is None:
            # The model answered, but the answer was empty / not JSON / missing
            # required fields. That is drift, and drift is what retries are for
            # -- it used to abandon the idea on attempt 1 while a WORSE outcome
            # (captions breaking hard rules) got all MAX_ATTEMPTS below.
            # There is no draft to hand back, so the description is reset to the
            # original brief rather than carrying correction feedback.
            if attempt == MAX_ATTEMPTS:
                logger.warning("crew_socialpost_unparseable_exhausted", attempts=attempt)
                return None
            logger.warning("crew_socialpost_unparseable_retrying", attempt=attempt)
            task.description = base_description
            continue
        violations = find_caption_violations(plan, target_keyword=target_keyword)
        if not violations:
            return plan
        if attempt == MAX_ATTEMPTS:
            logger.warning(
                "crew_socialpost_rule_violations_exhausted",
                attempts=attempt,
                violations=violations,
            )
            return None
        logger.warning(
            "crew_socialpost_rule_violations_retrying",
            attempt=attempt,
            violations=violations,
        )
        # Rebuild from the ORIGINAL description each time: appending to an
        # already-appended prompt would stack contradictory correction blocks
        # from earlier attempts.
        task.description = base_description + _correction_prompt(plan, violations)

    return None
