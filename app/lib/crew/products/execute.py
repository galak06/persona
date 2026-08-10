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

import json
import re

from crewai import Agent, Task
from pydantic import ValidationError

from lib.crew.products.models import ProductSelection
from lib.observability import get_logger

logger = get_logger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    """Strip a wrapping ` ```json ... ``` ` fence, if present -- same
    defensive posture as `lib.crew.writer.execute._strip_code_fence`."""
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match else text.strip()


def _parse_selection(raw: str | None) -> ProductSelection | None:
    """Parse+validate one selector task's raw text output against
    `ProductSelection`. `None` (logged) on missing output, invalid JSON, or a
    schema mismatch -- never raises, matching this module's "never crash the
    run" contract. An empty products list is a VALID selection, not a failure."""
    if not raw or not raw.strip():
        logger.warning("crew_products_empty_output")
        return None
    text = _strip_code_fence(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("crew_products_json_decode_failed", error=str(exc), raw_output=text)
        return None
    try:
        return ProductSelection.model_validate(payload)
    except ValidationError as exc:
        logger.warning("crew_products_schema_validation_failed", error=str(exc))
        return None


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
