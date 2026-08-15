"""Turn an approved candidate into a full structured seed JSON via Gemini.

No web search at this stage — this is the LLM filling in a known schema. The
system prompt is the content-ideator skill's ``## LLM Prompt: enrich`` section
(loaded via ``lib.skill_loader``) plus a Python-side ``ALLOWED CATEGORIES:``
line built from ``schema.ALLOWED_CATEGORIES``.

We use Gemini's structured-output JSON mode (responseMimeType=application/json
+ responseSchema) for reliability. The resulting dict is then re-validated
by ideator/schema.validate_seed() — Gemini schemas can be incomplete, our
validator is stricter.
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import httpx

from .research import Candidate
from .schema import ALLOWED_CATEGORIES

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent.parent

from lib.skill_loader import load_skill_prompt

logger = logging.getLogger(__name__)

_GEMINI_MODEL: Final[str] = os.getenv("GEMINI_ENRICHER_MODEL", "gemini-2.5-flash")
_GEMINI_ENDPOINT: Final[str] = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


@lru_cache(maxsize=1)
def _system_prompt() -> str:
    """Rendered content-ideator ``## LLM Prompt: enrich`` section, cached.

    The allowed-category set is NOT duplicated in the skill file: it is the
    same ``schema.ALLOWED_CATEGORIES`` constant the validator enforces, and is
    appended here as a trailing ``ALLOWED CATEGORIES:`` line so the two can
    never drift.

    Raises ``SkillPromptError`` (from ``lib.skill_loader``) on a missing or
    broken skill file — a deployment defect must abort loudly, never degrade
    into a promptless call.
    """
    base = load_skill_prompt("content-ideator", section="enrich")
    return f"{base}\n\nALLOWED CATEGORIES: {sorted(ALLOWED_CATEGORIES)}"


def _user_prompt(c: Candidate) -> str:
    return f"""\
Generate a complete recipe seed JSON for this approved candidate:

  Title:    {c.title}
  Category: {c.category}
  Why now:  {c.why_now}

Match the schema exactly. Output ONLY the JSON object — no fences, no preamble.
"""


_JSON_OBJECT_RE: Final[re.Pattern[str]] = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text, flags=re.IGNORECASE)
    match = _JSON_OBJECT_RE.search(cleaned)
    if not match:
        raise ValueError(f"no JSON object found in enricher response: {text[:300]!r}")
    return json.loads(match.group(0))


def enrich_to_seed(candidate: Candidate) -> dict[str, Any]:
    """Call Gemini, parse + return seed dict. Caller must run schema.validate_seed()."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in env")

    payload = {
        "systemInstruction": {"parts": [{"text": _system_prompt()}]},
        "contents": [{"role": "user", "parts": [{"text": _user_prompt(candidate)}]}],
        "generationConfig": {
            "temperature": 0.5,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",
        },
    }

    url = _GEMINI_ENDPOINT.format(model=_GEMINI_MODEL)
    logger.info("gemini enrich call model=%s title=%r", _GEMINI_MODEL, candidate.title)
    r = httpx.post(url, params={"key": api_key}, json=payload, timeout=180.0)
    if r.status_code >= 400:
        raise RuntimeError(f"gemini enrich HTTP {r.status_code}: {r.text[:500]}")

    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError(f"gemini enrich returned no candidates: {data!r}")
    parts = cands[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    if not text.strip():
        raise RuntimeError(f"gemini enrich returned empty text; parts={parts!r}")

    return _extract_json_object(text)
