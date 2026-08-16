"""Real `Crew(...).kickoff()` wrapper for the content-idea synthesis stage.

Same shape as `lib.crew.trends.execute.execute_trends_crew` and
`lib.crew.writer.execute`'s wrappers: the only function in this subpackage
that makes a real LLM/network call, returning `None` (logged, not raised) on
any failure so `lib.crew.scout`'s default wiring never crashes the run.
Tests always inject a fake `idea_execute_fn` instead of calling this.

Parses `task.output.raw` as JSON and validates it against `ScoutOutput`
directly, rather than reading `task.output.pydantic` (i.e. NOT using
`Task(output_pydantic=...)`) -- see `lib.crew.idea.agent`'s module docstring
for why: `crewai==1.15.12`'s `output_pydantic` path is currently incompatible
with DeepSeek's API (live-confirmed this build). The prompt
(`lib.crew.idea.agent._json_output_instructions`) already asks the model to
return raw JSON matching the real schema, so this is the same contract, just
validated on our side instead of crewai's.

`_parse_structured_output` now delegates to `lib.crew.structured_output`. It
used to be a local copy kept independent on purpose; the copies diverged into
three tiers of robustness instead of evolving independently, so every
hardening of `lib.crew.json_recovery` reached one stage out of eight. See
`docs/adr/0007-one-crew-output-parser.md`.
"""

from __future__ import annotations

from typing import TypeVar

from crewai import Agent, Task
from pydantic import BaseModel

from lib.crew.models import ScoutOutput
from lib.crew.structured_output import parse_structured_output
from lib.observability import get_logger

logger = get_logger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


def _parse_structured_output(raw: str | None, model: type[ModelT], *, event: str) -> ModelT | None:
    """Delegates to the one crew output parser -- see `lib.crew.structured_output`.

    Kept as a thin wrapper so this stage's log lines keep its own logger name.
    """
    return parse_structured_output(raw, model, event=event, log=logger)


def execute_idea_crew(agent: Agent, task: Task) -> ScoutOutput | None:
    """Run the real idea-synthesis `Crew(...).kickoff()`. `None` on any failure."""
    from crewai import Crew  # local import: keeps Crew construction next to its one use

    try:
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        crew.kickoff()
    except Exception as exc:  # CrewAI/LiteLLM/network errors
        logger.warning("crew_idea_kickoff_failed", error=str(exc))
        return None

    raw = task.output.raw if task.output else None
    return _parse_structured_output(raw, ScoutOutput, event="crew_idea")
