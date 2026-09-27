"""HTTP client for Jev decisions on OpenRouter -- never raises.

``POST https://openrouter.ai/api/alpha/decisions`` is not a chat/completions
endpoint, so it cannot ride CrewAI/LiteLLM or ``lib.llm_client.TextLLM``;
this is a dedicated ``httpx`` call following the same contract as
``lib.llm_client``'s providers: every failure (no key, HTTP error, timeout,
malformed body) is logged as a structured event and returns ``None``.

The key is read from ``OPENROUTER_API_KEY`` (engine-wide, in ``app/.env``).
Its value is never logged; a missing key is warned about ONCE per process
and every call becomes a cheap no-op.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from typing import Final

import httpx

from lib.decisions.jev_types import (
    JevResult,
    JsonDict,
    Question,
    parse_answers,
    questions_to_json,
    usage_cost,
)
from lib.llm_tracing import trace_llm_call
from lib.observability import get_logger

log = get_logger(__name__)

JEV_URL: Final = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL: Final = "typesafe/jev-1.13"
API_KEY_ENV: Final = "OPENROUTER_API_KEY"
TIMEOUT_SECONDS: Final = 5.0
# The model's context is 32k tokens; a post longer than this is truncated
# rather than rejected (a caption's first few thousand chars carry the topic).
MAX_STATE_CHARS: Final = 8000
_TRACE_NAME: Final = "jev-decision"

_missing_key_warned = False


def api_key_configured() -> bool:
    """True when ``OPENROUTER_API_KEY`` is set (the value is never returned)."""
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def warn_missing_key_once() -> None:
    """Log the missing-key warning at most once per process."""
    global _missing_key_warned
    if _missing_key_warned:
        return
    _missing_key_warned = True
    log.warning("jev_api_key_missing", env_var=API_KEY_ENV, effect="jev_gate_noop")


def _state_payload(state: str | JsonDict) -> str | JsonDict:
    if isinstance(state, str):
        return state[:MAX_STATE_CHARS]
    return state


def _post(payload: JsonDict, key: str) -> httpx.Response:
    return httpx.post(
        JEV_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=TIMEOUT_SECONDS,
    )


def decide(state: str | JsonDict, questions: Mapping[str, Question]) -> JevResult | None:
    """Ask Jev ``questions`` about ``state``; None on any failure.

    One request carries every question (batching several *states* into one
    request is undocumented, so callers make one call per item).
    """
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        warn_missing_key_once()
        return None
    if not questions:
        return None

    payload: JsonDict = {
        "model": JEV_MODEL,
        "state": _state_payload(state),
        "questions": questions_to_json(questions),
    }
    started = time.monotonic()
    try:
        response = trace_llm_call(
            _TRACE_NAME,
            model=JEV_MODEL,
            input_text=json.dumps(payload["state"], default=str)[:MAX_STATE_CHARS],
            call=lambda: _post(payload, key),
        )
    except httpx.TimeoutException:
        log.warning("jev_timeout", timeout_s=TIMEOUT_SECONDS)
        return None
    except httpx.HTTPError as exc:
        log.warning("jev_transport_error", error_type=type(exc).__name__)
        return None
    except Exception as exc:  # the never-raises contract covers the unforeseen too
        log.warning("jev_unexpected_error", error_type=type(exc).__name__)
        return None
    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        return _result_from_response(response, questions, latency_ms)
    except Exception as exc:
        log.warning("jev_unexpected_error", error_type=type(exc).__name__, stage="parse")
        return None


def _result_from_response(
    response: httpx.Response, questions: Mapping[str, Question], latency_ms: int
) -> JevResult | None:
    if response.status_code >= 400:
        # The body can echo request details; log the status and a short,
        # key-free prefix only.
        log.warning(
            "jev_http_error",
            status_code=response.status_code,
            body_prefix=response.text[:200],
            latency_ms=latency_ms,
        )
        return None
    try:
        body: object = response.json()
    except ValueError:
        log.warning("jev_malformed_response", reason="not_json", latency_ms=latency_ms)
        return None
    answers = parse_answers(questions, body)
    if answers is None or not isinstance(body, dict):
        log.warning("jev_malformed_response", reason="answers_invalid", latency_ms=latency_ms)
        return None
    raw_answers = body.get("answers")
    cost = usage_cost(body)
    model = body.get("model")
    log.info("jev_decided", latency_ms=latency_ms, cost_usd=cost, questions=sorted(questions))
    return JevResult(
        answers=answers,
        raw_answers=dict(raw_answers) if isinstance(raw_answers, dict) else {},
        model=model if isinstance(model, str) else JEV_MODEL,
        latency_ms=latency_ms,
        cost_usd=cost,
    )
