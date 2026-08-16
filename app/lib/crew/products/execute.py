"""Real `Crew(...).kickoff()` wrapper for the product-selector stage.

Same shape as `lib.crew.categorizer.execute.execute_categorizer_crew`: the
only function in this subpackage that makes a real LLM/network call,
returning `None` (logged, not raised) on any failure. Tests always inject a
fake `execute_fn` at the caller level instead of calling this directly --
same seam as `lib.crew.draft_category`'s `CategorizeFn` and
`lib.crew.writer.orchestrator`'s `StrategistExecuteFn`/`WriterExecuteFn`.

Parses `task.output.raw` as JSON and validates it against `ProductSelection`
directly, rather than reading `task.output.pydantic` (i.e. NOT using
`Task(output_pydantic=...)`) -- same upstream crewai/DeepSeek incompatibility
`lib.crew.writer.agent`'s module docstring documents for the strategist/
writer pair, and `lib.crew.categorizer.execute` documents for its own stage.
"""

from __future__ import annotations

from crewai import Agent, Task

from lib.crew.products.models import ProductSelection
from lib.crew.structured_output import parse_structured_output
from lib.observability import get_logger

logger = get_logger(__name__)


def _parse_selection(raw: str | None) -> ProductSelection | None:
    """Parse+validate one selector task's raw output against `ProductSelection`.

    An empty products list is a VALID selection, not a failure.

    `None` (logged) on missing output, unrecoverable JSON, or a schema
    mismatch -- never raises. Was plain `json.loads` after fence-stripping;
    it now goes through the one crew parser, so this stage finally gets the
    same recovery `writer` has always had.
    """
    return parse_structured_output(raw, ProductSelection, event="crew_products", log=logger)


def execute_product_selector_crew(agent: Agent, task: Task) -> ProductSelection | None:
    """Run the real product-selector `Crew(...).kickoff()`. `None` on any failure."""
    from crewai import Crew  # local import: keeps Crew construction next to its one use

    try:
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        crew.kickoff()
    except Exception as exc:  # CrewAI/LiteLLM/network errors
        logger.warning("crew_products_kickoff_failed", error=str(exc))
        return None

    raw = task.output.raw if task.output else None
    return _parse_selection(raw)
