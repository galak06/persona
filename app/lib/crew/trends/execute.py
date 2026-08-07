"""Real `Crew(...).kickoff()` wrapper for the market-trend scout stage.

The only function in this subpackage that makes a real LLM/network call,
returning `None` (logged, not raised) on any failure so callers (`lib.crew.
scout.run_crew_scout`) never crash the run. Tests always inject a fake
`trends_execute_fn` instead of calling this directly.

Parses `task.output.raw` as JSON and validates it against `TrendsOutput`
directly, rather than reading `task.output.pydantic` (i.e. NOT using
`Task(output_pydantic=...)`) -- see `lib.crew.trends.agent`'s module
docstring for why: `crewai==1.15.12`'s `output_pydantic` path is currently
incompatible with DeepSeek's API (live-confirmed, same upstream issue
`lib.crew.writer.agent` documents). The prompt
(`lib.crew.trends.agent._json_output_instructions`) already asks the model
to return raw JSON matching the real schema, so this is the same contract,
just validated on our side instead of crewai's.
"""

from __future__ import annotations

import json
import re

from crewai import Agent, Task
from pydantic import ValidationError

from lib.crew.trends.models import TrendsOutput
from lib.observability import get_logger

logger = get_logger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    """Strip a wrapping ` ```json ... ``` ` fence, if present -- LLMs add one
    even when explicitly told not to often enough to be worth defending
    against rather than failing the whole run over it."""
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match else text.strip()


def _iter_balanced_json_object_candidates(text: str) -> list[str]:
    """Return every top-level, brace-balanced `{...}` substring found in
    `text`, in the order they appear.

    Brace depth is tracked while skipping over JSON string contents (so a
    literal `{`/`}` inside a quoted string, e.g. in a `"reason": "..."`
    value, doesn't throw off the count). This is more robust than a naive
    first-`{`-to-last-`}` slice: a real DeepSeek response was observed to
    contain a complete JSON object followed by more chain-of-thought prose
    ("Wait, let me verify this is complete...") followed by a second,
    final JSON object -- slicing from the first `{` to the last `}` in
    that case captures both objects plus the prose between them, which
    isn't valid JSON on its own."""
    candidates: list[str] = []
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : i + 1])
                start = None
    return candidates


def _extract_json_payload(text: str) -> object | None:
    """Find and parse the most likely intended JSON object embedded in
    `text`.

    Reasoning models (e.g. DeepSeek) often emit tens of KB of visible
    chain-of-thought prose around their final JSON answer -- sometimes
    even a complete-but-superseded JSON object mid-reasoning before the
    real final one. Candidates are tried last-to-first (the final one in
    the text is the model's actual answer -- it may restate/replace an
    earlier draft), returning the payload from the first one that parses
    as valid JSON. `None` if no candidate parses."""
    for candidate in reversed(_iter_balanced_json_object_candidates(text)):
        try:
            parsed: object = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return parsed
    return None


def _parse_structured_output(raw: str | None) -> TrendsOutput | None:
    """Parse+validate the trend scout task's raw text output against
    `TrendsOutput`. `None` (logged) on missing output, invalid JSON, or a
    schema mismatch -- never raises, matching this module's "never crash the
    run" contract."""
    if not raw or not raw.strip():
        logger.warning("crew_trends_empty_output")
        return None
    text = _strip_code_fence(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        # The model may have wrapped the JSON in reasoning prose (e.g.
        # DeepSeek's visible chain-of-thought before the final answer).
        # Fall back to locating a parseable JSON object embedded in the
        # text before giving up.
        payload = _extract_json_payload(text)
        if payload is None:
            _log_json_decode_failed(exc, text)
            return None
    try:
        return TrendsOutput.model_validate(payload)
    except ValidationError as exc:
        logger.warning("crew_trends_schema_validation_failed", error=str(exc))
        return None


def _log_json_decode_failed(exc: json.JSONDecodeError, text: str) -> None:
    # Full text, not a short excerpt -- a truncated excerpt was proven
    # useless for real diagnosis in the writer pipeline (see
    # `lib.crew.writer.execute`'s note); this is a structured JSON log
    # field, not stdout prose, so a large string value is fine here.
    logger.warning("crew_trends_json_decode_failed", error=str(exc), raw_output=text)


def execute_trends_crew(agent: Agent, task: Task) -> TrendsOutput | None:
    """Run the real trend-scout `Crew(...).kickoff()`. `None` on any failure."""
    from crewai import Crew  # local import: keeps Crew construction next to its one use

    try:
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        crew.kickoff()
    except Exception as exc:  # CrewAI/LiteLLM/network errors
        logger.warning("crew_trends_kickoff_failed", error=str(exc))
        return None

    raw = task.output.raw if task.output else None
    return _parse_structured_output(raw)
