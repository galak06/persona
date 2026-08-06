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

import json
import re
from typing import TypeVar

from crewai import Agent, Task
from pydantic import BaseModel, ValidationError

from lib.crew.writer.models import ContentBrief, WrittenPost
from lib.observability import get_logger

logger = get_logger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    """Strip a wrapping ` ```json ... ``` ` fence, if present -- LLMs add one
    even when explicitly told not to often enough to be worth defending
    against rather than failing the whole run over it."""
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match else text.strip()


def _parse_structured_output(raw: str | None, model: type[ModelT], *, event: str) -> ModelT | None:
    """Parse+validate one CrewAI task's raw text output against `model`.
    `None` (logged) on missing output, invalid JSON, or a schema mismatch --
    never raises, matching this module's "never crash the run" contract."""
    if not raw or not raw.strip():
        logger.warning(f"{event}_empty_output")
        return None
    text = _strip_code_fence(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        # Full text, not a short excerpt -- a 200-char excerpt was proven
        # useless for real diagnosis (two consecutive live failures on the
        # same brief, error position always well past char 200, no way to
        # see what was actually malformed). This is a structured JSON log
        # field, not stdout prose -- large string values are fine here.
        logger.warning(f"{event}_json_decode_failed", error=str(exc), raw_output=text)
        return None
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        logger.warning(f"{event}_schema_validation_failed", error=str(exc))
        return None


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
