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

from crewai import Agent, Task

from lib.crew.structured_output import parse_structured_output
from lib.crew.trends.models import TrendsOutput
from lib.observability import get_logger

logger = get_logger(__name__)


def _parse_structured_output(raw: str | None) -> TrendsOutput | None:
    """Parse+validate the trend scout task's raw output against `TrendsOutput`.

    Model and event are bound here rather than passed, matching this stage's
    long-standing signature; the parsing itself is the shared one.
    """
    return parse_structured_output(raw, TrendsOutput, event="crew_trends", log=logger)


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
