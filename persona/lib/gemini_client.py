"""Thin Gemini ``generateContent`` client shared by the drafting layers.

Owns the HTTP transport once — key check, endpoint, ``httpx`` POST, candidate/
parts extraction — plus best-effort Langfuse tracing, so ``lib.reply_drafter``
(plain-text drafts) and ``lib.draft_helper`` (structured engage/decline
decisions) don't each re-implement it. Two public calls:

  ``_call_gemini(prompt, ...)``      -> ``str | None``  — first candidate's text
  ``_call_gemini_json(prompt, ...)`` -> ``dict | None`` — {engage, comment, reason}

Both return ``None`` on any failure (missing key, non-2xx, no candidates,
malformed/incomplete JSON) so callers fall back or skip. Every call is traced
to Langfuse via ``lib.llm_tracing`` when configured — best-effort, never fatal.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from llm_tracing import trace_llm_call

logger = logging.getLogger(__name__)

_GEMINI_MODEL = os.getenv("GEMINI_REPLY_MODEL", "gemini-2.5-flash")
_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_ENGAGE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "engage": {"type": "boolean"},
        "comment": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["engage", "comment", "reason"],
}


def _base_payload(prompt: str, *, max_tokens: int) -> dict[str, Any]:
    """The generateContent payload common to both call styles.

    gemini-2.5-flash defaults to "thinking" mode, which consumes output budget
    before writing any visible text; disabled here (``thinkingBudget=0``) since
    these short drafting tasks don't benefit from it.
    """
    return {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }


def _gemini_request(
    payload: dict[str, Any], *, trace_name: str, prompt: str
) -> dict[str, Any] | None:
    """POST ``payload`` to Gemini and return the parsed response JSON, or
    ``None`` on any failure, wrapped in a best-effort Langfuse generation trace.

    Shared by ``_call_gemini`` and ``_call_gemini_json`` so the HTTP + trace
    mechanics live in exactly one place.
    """
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        logger.warning("gemini: GEMINI_API_KEY not set — skipping call")
        return None
    url = _GEMINI_ENDPOINT.format(model=_GEMINI_MODEL)

    def _do_call() -> dict[str, Any] | None:
        try:
            r = httpx.post(url, params={"key": key}, json=payload, timeout=30.0)
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
        trace_name, model=_GEMINI_MODEL, input_text=prompt, call=_do_call
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


def _call_gemini(prompt: str, *, max_tokens: int = 1200) -> str | None:
    """Plain-text draft: the first candidate's text, or ``None`` on failure."""
    data = _gemini_request(
        _base_payload(prompt, max_tokens=max_tokens), trace_name="gemini-draft", prompt=prompt
    )
    return _first_candidate_text(data) if data else None


def _call_gemini_json(prompt: str, *, max_tokens: int = 400) -> dict[str, Any] | None:
    """Structured ``{engage, comment, reason}`` decision via Gemini's
    ``responseMimeType``/``responseSchema`` fields, so the model's engage/
    decline decision is a first-class field. Returns the parsed dict, or
    ``None`` on any failure (missing key, non-2xx, no candidates, malformed or
    incomplete JSON) — same "caller falls back" contract as ``_call_gemini``.
    """
    payload = _base_payload(prompt, max_tokens=max_tokens)
    payload["generationConfig"]["responseMimeType"] = "application/json"
    payload["generationConfig"]["responseSchema"] = _ENGAGE_RESPONSE_SCHEMA
    data = _gemini_request(payload, trace_name="gemini-engage-decision", prompt=prompt)
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
    if not isinstance(parsed, dict) or "engage" not in parsed:
        logger.warning("gemini json response missing 'engage' field: %s", text[:200])
        return None
    return parsed
