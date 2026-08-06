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

import json
import re

from crewai import Agent, Task
from pydantic import ValidationError

from lib.crew.editor.models import QualityVerdict
from lib.observability import get_logger

logger = get_logger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    """Strip a wrapping ` ```json ... ``` ` fence, if present -- same
    defensive posture as `lib.crew.writer.execute._strip_code_fence`."""
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match else text.strip()


def _parse_verdict(raw: str | None) -> QualityVerdict | None:
    """Parse+validate one editor task's raw text output against
    `QualityVerdict`. `None` (logged) on missing output, invalid JSON, or a
    schema mismatch -- never raises, matching this module's "never crash the
    run" contract."""
    if not raw or not raw.strip():
        logger.warning("crew_editor_empty_output")
        return None
    text = _strip_code_fence(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("crew_editor_json_decode_failed", error=str(exc), raw_excerpt=text[:200])
        return None
    try:
        return QualityVerdict.model_validate(payload)
    except ValidationError as exc:
        logger.warning("crew_editor_schema_validation_failed", error=str(exc))
        return None


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
