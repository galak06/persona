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

Parsing the model's JSON is delegated to `lib.crew.structured_output`. The
local `_strip_code_fence`/`_extract_json_payload` copies were kept independent
on purpose; in practice they diverged into three tiers of robustness, leaving
this stage without the lenient repair `writer` had. See
`docs/adr/0007-one-crew-output-parser.md`.
"""

from __future__ import annotations

from typing import TypeVar

from crewai import Agent, Task
from pydantic import BaseModel

from lib.crew.reels.models import REEL_BEAT_COUNT, ReelPlan
from lib.crew.structured_output import parse_structured_output
from lib.observability import get_logger

logger = get_logger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


def _parse_structured_output(raw: str | None, model: type[ModelT], *, event: str) -> ModelT | None:
    """Delegates to the one crew output parser -- see `lib.crew.structured_output`.

    Kept as a thin wrapper so this stage's log lines keep its own logger name.
    """
    return parse_structured_output(raw, model, event=event, log=logger)


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
        task.description + f"\n\n---\nPREVIOUS DRAFT WAS REJECTED: it had {len(plan.beats)} beats, "
        f"not exactly {REEL_BEAT_COUNT}. Re-draft with EXACTLY {REEL_BEAT_COUNT} beats: "
        "hook, point, point, point, CTA -- no more, no fewer."
    )
    retry_plan = _kickoff_and_parse(agent, task)
    if retry_plan is None or len(retry_plan.beats) != REEL_BEAT_COUNT:
        logger.warning("crew_reels_beat_count_violation_after_retry")
        return None
    return retry_plan
