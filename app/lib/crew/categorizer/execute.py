"""Real `Crew(...).kickoff()` wrapper for the post-categorizer stage.

Same shape as `lib.crew.editor.execute.execute_editor_crew`: the only
function in this subpackage that makes a real LLM/network call, returning
`None` (logged, not raised) on any failure. Tests always inject a fake
`execute_fn` instead of calling this directly (see
`lib.crew.draft._resolve_category_id`).

Parses `task.output.raw` as JSON and validates it against `CategoryChoice`
directly, rather than reading `task.output.pydantic` (i.e. NOT using
`Task(output_pydantic=...)`) -- same upstream crewai/DeepSeek incompatibility
`lib.crew.writer.agent`'s module docstring documents for the strategist/
writer pair, and `lib.crew.editor.execute` documents independently for
`QualityVerdict`'s different schema shape.
"""

from __future__ import annotations

from crewai import Agent, Task

from lib.crew.categorizer.models import CategoryChoice
from lib.crew.structured_output import parse_structured_output
from lib.observability import get_logger

logger = get_logger(__name__)


def _parse_choice(raw: str | None) -> CategoryChoice | None:
    """Parse+validate one categorizer task's raw output against `CategoryChoice`.

    `None` (logged) on missing output, unrecoverable JSON, or a schema
    mismatch -- never raises. Was plain `json.loads` after fence-stripping;
    it now goes through the one crew parser, so this stage finally gets the
    same recovery `writer` has always had.
    """
    return parse_structured_output(raw, CategoryChoice, event="crew_categorizer", log=logger)


def execute_categorizer_crew(agent: Agent, task: Task) -> CategoryChoice | None:
    """Run the real categorizer `Crew(...).kickoff()`. `None` on any failure."""
    from crewai import Crew  # local import: keeps Crew construction next to its one use

    try:
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        crew.kickoff()
    except Exception as exc:  # CrewAI/LiteLLM/network errors
        logger.warning("crew_categorizer_kickoff_failed", error=str(exc))
        return None

    raw = task.output.raw if task.output else None
    return _parse_choice(raw)
