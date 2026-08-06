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

import json
import re

from crewai import Agent, Task
from pydantic import ValidationError

from lib.crew.categorizer.models import CategoryChoice
from lib.observability import get_logger

logger = get_logger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    """Strip a wrapping ` ```json ... ``` ` fence, if present -- same
    defensive posture as `lib.crew.writer.execute._strip_code_fence`."""
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match else text.strip()


def _parse_choice(raw: str | None) -> CategoryChoice | None:
    """Parse+validate one categorizer task's raw text output against
    `CategoryChoice`. `None` (logged) on missing output, invalid JSON, or a
    schema mismatch -- never raises, matching this module's "never crash the
    run" contract."""
    if not raw or not raw.strip():
        logger.warning("crew_categorizer_empty_output")
        return None
    text = _strip_code_fence(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("crew_categorizer_json_decode_failed", error=str(exc), raw_output=text)
        return None
    try:
        return CategoryChoice.model_validate(payload)
    except ValidationError as exc:
        logger.warning("crew_categorizer_schema_validation_failed", error=str(exc))
        return None


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
