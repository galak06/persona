"""Real `Crew(...).kickoff()` wrapper for the quality-editor stage.

Same shape as `lib.crew.writer.execute.execute_writer_crew`: the only
function in this subpackage that makes a real LLM/network call, returning
`None` (logged, not raised) on any failure. Tests always inject a fake
`execute_fn` instead of calling this directly (see `lib.crew.validate`).

Parses `task.output.raw` as JSON and validates it against `QualityVerdict`
directly, rather than reading `task.output.pydantic` (i.e. NOT using
`Task(output_pydantic=...)`) -- live-confirmed this build (an isolated
repro using this exact agent/task against DeepSeek) that `crewai==1.15.12`
still sends OpenAI's strict `json_schema` `response_format` for
`deepseek/...` models even for `QualityVerdict`'s schema (a different shape
than `ContentBrief`/`WrittenPost`), which DeepSeek's API rejects outright --
the same upstream crewai/DeepSeek incompatibility
`lib.crew.writer.agent`'s module docstring documents, confirmed
independently here rather than assumed. Routed around the same way: raw-JSON
prompt instructions (`lib.crew.editor.agent._json_output_instructions`),
parsed manually below.
"""

from __future__ import annotations

from crewai import Agent, Task

from lib.crew.editor.models import QualityVerdict
from lib.crew.structured_output import parse_structured_output
from lib.observability import get_logger

logger = get_logger(__name__)


def _parse_verdict(raw: str | None) -> QualityVerdict | None:
    """Parse+validate one editor task's raw output against `QualityVerdict`.

    `None` (logged) on missing output, unrecoverable JSON, or a schema
    mismatch -- never raises. Was plain `json.loads` after fence-stripping;
    it now goes through the one crew parser, so this stage finally gets the
    same recovery `writer` has always had.
    """
    return parse_structured_output(raw, QualityVerdict, event="crew_editor", log=logger)


def execute_editor_crew(agent: Agent, task: Task) -> QualityVerdict | None:
    """Run the real editor `Crew(...).kickoff()`. `None` on any failure."""
    from crewai import Crew  # local import: keeps Crew construction next to its one use

    try:
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        crew.kickoff()
    except Exception as exc:  # CrewAI/LiteLLM/network errors
        logger.warning("crew_editor_kickoff_failed", error=str(exc))
        return None

    raw = task.output.raw if task.output else None
    return _parse_verdict(raw)
