"""One parser for every crew stage's structured LLM output.

Each of the eight stages under `lib/crew/` carried its own copy of this logic,
and three of them (`idea`, `reels`, `socialpost`) said so on purpose::

    "a local, independent copy -- deliberately NOT imported from
     `lib.crew.trends.execute` or `lib.crew.writer.execute` -- matching this
     repo's existing convention of keeping independently-evolving pipelines'
     parsing helpers separate rather than sharing a common utility module."

The copies did not evolve independently; they diverged into three tiers of
robustness behind one nominal contract:

* `writer` -- balanced-brace extraction **and** `json_recovery.loads_lenient`
* `idea`, `trends`, `reels`, `socialpost` -- extraction, no lenient repair
* `editor`, `categorizer`, `products` -- fence-stripping, then plain
  `json.loads`

That is a correctness difference, not a stylistic one. The same malformed model
response is recovered in one stage and discarded in another, and every
hardening of `json_recovery` -- "a stray `,` can't bin a brief", "a quoted word
can't bin a post" -- reached exactly one of the eight. Consolidating is what
makes those fixes protect the pipeline rather than one stage of it.

This module deliberately does NOT modify `lib/crew/json_recovery.py`; it only
widens its reach. The recovery primitives stay where they are.

See `docs/adr/0007-one-crew-output-parser.md`.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from lib.crew.json_recovery import loads_lenient, strip_code_fence
from lib.observability import get_logger

ModelT = TypeVar("ModelT", bound=BaseModel)

_default_logger = get_logger(__name__)


class _Logger(Protocol):
    """The structlog-style surface this parser needs from a stage's logger."""

    def warning(self, event: str, **kwargs: object) -> object: ...


def parse_structured_output(
    raw: str | None,
    model: type[ModelT],
    *,
    event: str,
    log: _Logger | None = None,
) -> ModelT | None:
    """Parse and validate one CrewAI task's raw text output against `model`.

    Returns `None` -- never raises -- on missing output, unrecoverable JSON, or
    a schema mismatch, matching the "never crash the run" contract every stage
    already had. `event` prefixes the log lines so a failure is attributable to
    the stage that produced it; `log` defaults to this module's logger but
    stages pass their own so the line keeps their name.
    """
    logger = log or _default_logger

    if not raw or not raw.strip():
        logger.warning(f"{event}_empty_output")
        return None

    text = strip_code_fence(raw)
    payload, repair = loads_lenient(text)

    if payload is None:
        # Full text, not a short excerpt -- a 200-char excerpt was proven
        # useless for real diagnosis (two consecutive live failures on the same
        # brief, error position always well past char 200, no way to see what
        # was actually malformed). This is a structured JSON log field, not
        # stdout prose, so a large string value is fine here.
        logger.warning(f"{event}_json_decode_failed", raw_output=text)
        return None

    if repair is not None:
        # Warning, not info: the output is usable, but the model emitted
        # damaged JSON and the recovery may have left an artifact in the body
        # (see `lib.crew.json_recovery.repair_invalid_escapes`). A recovered
        # parse must never look like a clean one.
        logger.warning(f"{event}_json_recovered", repair=repair)

    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        logger.warning(f"{event}_schema_validation_failed", error=str(exc))
        return None
