"""Real `Crew(...).kickoff()` wrappers for the strategist/writer stages.

Split out of `lib.crew.writer.orchestrator` purely for file-size discipline.
Same shape as `lib.crew.scout.execute_scout_crew`: the only functions in
this subpackage that make a real LLM/network call, each returning `None`
(logged, not raised) on any failure so `lib.crew.writer.orchestrator`'s
default wiring never crashes the run. Tests always inject a fake
`execute_fn` instead of calling these.

Parses `task.output.raw` as JSON and validates it against the target Pydantic
model directly, rather than reading `task.output.pydantic` (i.e. NOT using
`Task(output_pydantic=...)`) -- see `lib.crew.writer.agent`'s module
docstring for why: `crewai==1.15.12`'s `output_pydantic` path is currently
incompatible with DeepSeek's API (live-confirmed this build). Both prompts
(`lib.crew.writer.agent._json_output_instructions`) already ask the model to
return raw JSON matching the real schema, so this is the same contract,
just validated on our side instead of crewai's.
"""

from __future__ import annotations

from typing import TypeVar

from crewai import Agent, Task
from pydantic import BaseModel

from lib.crew.structured_output import parse_structured_output
from lib.crew.writer.models import ContentBrief, WrittenPost
from lib.observability import get_logger

logger = get_logger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


def _parse_structured_output(raw: str | None, model: type[ModelT], *, event: str) -> ModelT | None:
    """Delegates to the one crew output parser -- see `lib.crew.structured_output`.

    Kept as a thin wrapper so this stage's log lines keep its own logger name.
    """
    return parse_structured_output(raw, model, event=event, log=logger)


def execute_strategist_crew(agent: Agent, task: Task) -> ContentBrief | None:
    """Run the real strategist `Crew(...).kickoff()`. `None` on any failure."""
    from crewai import Crew  # local import: keeps Crew construction next to its one use

    try:
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        crew.kickoff()
    except Exception as exc:  # CrewAI/LiteLLM/network errors
        logger.warning("crew_writer_strategist_kickoff_failed", error=str(exc))
        return None

    raw = task.output.raw if task.output else None
    return _parse_structured_output(raw, ContentBrief, event="crew_writer_strategist")


def execute_writer_crew(agent: Agent, task: Task) -> WrittenPost | None:
    """Run the real writer `Crew(...).kickoff()`. `None` on any failure."""
    from crewai import Crew

    try:
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        crew.kickoff()
    except Exception as exc:
        logger.warning("crew_writer_writer_kickoff_failed", error=str(exc))
        return None

    raw = task.output.raw if task.output else None
    return _parse_structured_output(raw, WrittenPost, event="crew_writer_writer")
