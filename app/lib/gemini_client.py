"""Thin Gemini ``generateContent`` client — the Gemini half of ``lib.llm_client``.

Owns the HTTP transport once — key check, endpoint, ``httpx`` POST, candidate/
parts extraction — plus best-effort Langfuse tracing (``lib.llm_tracing``,
never fatal). Public surface:

  ``_call_gemini(prompt, ...)``      -> ``str | None``  — first candidate's text
  ``call_json(prompt, schema=...)``  -> ``dict | None`` — caller-supplied schema
  ``call_json_async(...)``           -> async twin of ``call_json``, with backoff

All return ``None`` on any failure (missing key, non-2xx, no candidates,
malformed/incomplete JSON) so callers fall back or skip. The sync calls take
an optional ``system=`` string, sent as ``systemInstruction`` — the skill
path (``lib.draft_helper.SkillDrafter``) splits standing voice rules (system)
from per-post context (user). Omitted, payloads are byte-identical to before,
so every existing caller is unchanged.

Nothing engagement-specific lives here any more: the drafting flows reach this
module through ``lib.llm_client.GeminiLLM`` (so the provider is swappable) and
supply their own response schema — the ``{engage, comment, reason}`` one lives
with its prompt envelope in ``lib.draft_prompts``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from typing import Any

import httpx

from llm_tracing import atrace_llm_call, trace_llm_call

logger = logging.getLogger(__name__)

_GEMINI_MODEL = os.getenv("GEMINI_REPLY_MODEL", "gemini-3.6-flash")


def _thinking_budget() -> int | None:
    """Explicit thinkingBudget, or None to omit the field (see _base_payload).

    Omitted by default because `thinkingBudget: 0` is rejected by the Gemini
    3.x family with 400 INVALID_ARGUMENT — that family cannot disable thinking
    — and this client turns any such failure into an empty draft.

    Read per call rather than cached at import so the value stays overridable
    at runtime (and testable via monkeypatch).
    """
    raw = os.getenv("GEMINI_THINKING_BUDGET", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("gemini: ignoring non-integer GEMINI_THINKING_BUDGET=%r", raw)
        return None


_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# 429 = per-minute quota; 500/503 = transient upstream. Everything else
# (400 bad schema, 403 bad key) is a bug or a config error — retrying it
# just burns quota, so those fail fast.
_RETRYABLE_STATUS = frozenset({429, 500, 503})
_BACKOFF_BASE_SEC = 2.0
_BACKOFF_MAX_SEC = 60.0


async def _sleep_backoff(attempt: int, *, retry_after: str | None = None) -> None:
    """Sleep before the next attempt: server-directed if the API said so,
    otherwise exponential from 2s with full jitter to avoid a thundering herd
    when a whole fan-out batch hits quota at the same moment."""
    if retry_after:
        try:
            await asyncio.sleep(min(float(retry_after), _BACKOFF_MAX_SEC))
            return
        except ValueError:
            pass  # Non-numeric (HTTP-date) Retry-After — fall through to backoff.
    delay = min(_BACKOFF_BASE_SEC * (2**attempt), _BACKOFF_MAX_SEC)
    await asyncio.sleep(random.uniform(delay / 2, delay))


def _base_payload(
    prompt: str, *, max_tokens: int, temperature: float = 0.7, system: str | None = None
) -> dict[str, Any]:
    """The generateContent payload common to all call styles.

    "Thinking" mode consumes output budget before any visible text is written,
    so this used to pin ``thinkingBudget=0``. That is REJECTED by Gemini 3.x
    (400 INVALID_ARGUMENT — the family cannot disable thinking), which this
    client surfaces as an empty draft, indistinguishable from the agent
    declining to engage. The budget is therefore omitted by default, which is
    accepted by every current model; set ``GEMINI_THINKING_BUDGET`` to a
    non-negative value to cap or disable it on models that still allow that
    (the 2.5 family). ``temperature`` defaults to 0.7 (the drafting value);
    extraction-style callers pass lower. When ``system`` is set it is sent as
    ``systemInstruction`` (the same shape
    ``recipe-publisher/ideator/research.py`` uses).
    """
    generation_config: dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_tokens,
    }
    budget = _thinking_budget()
    if budget is not None:
        generation_config["thinkingConfig"] = {"thinkingBudget": budget}

    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    return payload


def _json_payload(
    prompt: str,
    *,
    schema: dict[str, Any],
    max_tokens: int,
    temperature: float,
    system: str | None = None,
) -> dict[str, Any]:
    """A ``_base_payload`` pinned to JSON output against ``schema``."""
    payload = _base_payload(prompt, max_tokens=max_tokens, temperature=temperature, system=system)
    payload["generationConfig"]["responseMimeType"] = "application/json"
    payload["generationConfig"]["responseSchema"] = schema
    return payload


def _api_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        logger.warning("gemini: GEMINI_API_KEY not set — skipping call")
        return None
    return key


def _parse_json_response(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract/parse the JSON object out of a generateContent response;
    ``None`` on no candidates, empty text, invalid JSON, or non-object."""
    if not data:
        return None
    text = _first_candidate_text(data)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("gemini json response not valid JSON: %s", text[:200])
        return None
    if not isinstance(parsed, dict):
        logger.warning("gemini json response is not an object: %s", text[:200])
        return None
    return parsed


def _gemini_request(
    payload: dict[str, Any], *, trace_name: str, prompt: str, system: str | None = None
) -> dict[str, Any] | None:
    """POST ``payload`` to Gemini and return the parsed response JSON, or
    ``None`` on any failure, wrapped in a best-effort Langfuse generation
    trace. When ``system`` is present the trace input carries both halves
    (``{"system": ..., "user": ...}``) so Langfuse shows the full prompt.
    """
    key = _api_key()
    if key is None:
        return None
    url = _GEMINI_ENDPOINT.format(model=_GEMINI_MODEL)
    input_text: str | dict[str, str] = {"system": system, "user": prompt} if system else prompt

    def _do_call() -> dict[str, Any] | None:
        try:
            r = httpx.post(url, headers={"x-goog-api-key": key}, json=payload, timeout=30.0)
            if r.status_code >= 400:
                logger.warning("gemini HTTP %s: %s", r.status_code, r.text[:200])
                return None
            data = r.json()
            if isinstance(data, dict):
                return data
            return None
        except Exception as e:
            logger.warning("gemini call failed: %s", e)
            return None

    result: dict[str, Any] | None = trace_llm_call(
        trace_name, model=_GEMINI_MODEL, input_text=input_text, call=_do_call
    )
    return result


def _first_candidate_text(data: dict[str, Any]) -> str | None:
    """First non-empty text part of the first candidate, or ``None``."""
    cands = data.get("candidates") or []
    if not cands:
        return None
    parts = cands[0].get("content", {}).get("parts", [])
    for p in parts:
        text = (p.get("text") or "").strip()
        if text:
            return text
    return None


def _call_gemini(
    prompt: str,
    *,
    max_tokens: int = 1200,
    temperature: float = 0.7,
    system: str | None = None,
    trace_name: str = "gemini-draft",
) -> str | None:
    """Plain-text draft: the first candidate's text, or ``None`` on failure.

    ``temperature`` and ``trace_name`` default to the historical drafting
    values, so callers that omit them behave exactly as before;
    ``lib.llm_client.GeminiLLM`` threads its ``LLMRequest`` values through.
    """
    data = _gemini_request(
        _base_payload(prompt, max_tokens=max_tokens, temperature=temperature, system=system),
        trace_name=trace_name,
        prompt=prompt,
        system=system,
    )
    return _first_candidate_text(data) if data else None


def call_json(
    prompt: str,
    *,
    schema: dict[str, Any],
    max_tokens: int = 1200,
    temperature: float = 0.7,
    trace_name: str = "gemini-json",
    system: str | None = None,
) -> dict[str, Any] | None:
    """Structured JSON against a caller-supplied ``schema`` (a Gemini
    ``responseSchema``, an OpenAPI-3 subset — no ``$ref``). Returns the parsed
    object, or ``None`` on any failure. Callers own validating the shape."""
    data = _gemini_request(
        _json_payload(
            prompt, schema=schema, max_tokens=max_tokens, temperature=temperature, system=system
        ),
        trace_name=trace_name,
        prompt=prompt,
        system=system,
    )
    return _parse_json_response(data)


async def call_json_async(
    prompt: str,
    *,
    schema: dict[str, Any],
    max_tokens: int = 1200,
    temperature: float = 0.7,
    trace_name: str = "gemini-json",
    retries: int = 3,
) -> dict[str, Any] | None:
    """Async twin of `call_json`, with built-in backoff for 429/5xx.

    Built for fan-out over many items, where per-minute quota hits are
    expected: honours ``Retry-After``, otherwise doubles from 2s with jitter.
    Exhausting ``retries`` returns ``None`` like every other failure."""
    key = _api_key()
    if key is None:
        return None
    url = _GEMINI_ENDPOINT.format(model=_GEMINI_MODEL)
    payload = _json_payload(prompt, schema=schema, max_tokens=max_tokens, temperature=temperature)

    async def _do_call() -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=60.0) as client:
            for attempt in range(retries + 1):
                try:
                    r = await client.post(url, headers={"x-goog-api-key": key}, json=payload)
                except httpx.HTTPError as e:
                    logger.warning("gemini async call failed: %s", e)
                    if attempt >= retries:
                        return None
                    await _sleep_backoff(attempt)
                    continue

                if r.status_code in _RETRYABLE_STATUS and attempt < retries:
                    logger.warning(
                        "gemini HTTP %s (attempt %s/%s) — backing off",
                        r.status_code,
                        attempt + 1,
                        retries,
                    )
                    await _sleep_backoff(attempt, retry_after=r.headers.get("Retry-After"))
                    continue
                if r.status_code >= 400:
                    logger.warning("gemini HTTP %s: %s", r.status_code, r.text[:200])
                    return None
                try:
                    data = r.json()
                except ValueError:
                    logger.warning("gemini async response was not JSON")
                    return None
                return data if isinstance(data, dict) else None
        return None

    data = await atrace_llm_call(trace_name, model=_GEMINI_MODEL, input_text=prompt, call=_do_call)
    return _parse_json_response(data)
