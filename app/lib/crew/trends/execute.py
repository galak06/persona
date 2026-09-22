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


DEFAULT_MAX_ATTEMPTS = 3

# Appended to the task description on a retry. The base prompt already says
# "no commentary before or after it" (`agent._json_output_instructions`) and
# the model ignores it anyway, so repeating that alone would be pointless --
# this names the specific failure and caps the field that actually blew the
# budget.
RETRY_INSTRUCTION = (
    "\n\nCRITICAL -- your previous response could not be parsed. You began with "
    "reasoning prose, which consumed the output budget and left the JSON "
    "truncated mid-object, so NOTHING was usable. This time: your very first "
    "character MUST be '{' and your very last MUST be '}'. Write no reasoning, "
    "no restatement of the inputs, no explanation -- before or after. Keep every "
    "`reason` field under 200 characters so the object cannot run out of room."
)


def execute_trends_crew(
    agent: Agent, task: Task, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS
) -> TrendsOutput | None:
    """Run the real trend-scout `Crew(...).kickoff()`. `None` on any failure.

    Retried, because this stage's dominant failure mode is not a bug but a
    dice roll: DeepSeek intermittently emits a long chain-of-thought preamble
    before the JSON, runs out of output budget, and truncates mid-object. A
    35,001-character response ending `"keyword": "d` -- 23 open braces to 21
    closed -- is unrecoverable no matter how good the repair layer is, and it
    zeroes the whole scout run (no signals -> no ideas -> exit 1).

    The same prompt succeeds on a re-roll, so one failure is not evidence the
    request is impossible. Retries also cover kickoff-level network errors,
    for the same reason the engager adapters retry navigation.

    `task.description` is restored before returning: the caller owns that
    object and must not be left carrying retry scaffolding.
    """
    from crewai import Crew  # local import: keeps Crew construction next to its one use

    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    original_description = task.description
    try:
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                task.description = original_description + RETRY_INSTRUCTION

            try:
                crew = Crew(agents=[agent], tasks=[task], verbose=False)
                crew.kickoff()
            except Exception as exc:  # CrewAI/LiteLLM/network errors
                logger.warning(
                    "crew_trends_kickoff_failed",
                    error=str(exc),
                    attempt=attempt,
                    max_attempts=max_attempts,
                )
                continue

            raw = task.output.raw if task.output else None
            parsed = _parse_structured_output(raw)
            if parsed is not None:
                if attempt > 1:
                    logger.info("crew_trends_recovered_on_retry", attempt=attempt)
                return parsed

            logger.warning(
                "crew_trends_parse_failed_retrying",
                attempt=attempt,
                max_attempts=max_attempts,
                raw_length=len(raw or ""),
            )
    finally:
        task.description = original_description

    logger.error("crew_trends_all_attempts_failed", max_attempts=max_attempts)
    return None
