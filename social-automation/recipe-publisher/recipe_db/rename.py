"""Brand-voice recipe renaming via Gemini.

Generates an original recipe title that keeps the dish meaning but reads
differently from the scraped source title, so published names are never copied
verbatim from the source. Reads ``GEMINI_API_KEY`` from the environment.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable

import httpx

logger = logging.getLogger("recipe_db.rename")

# A namer maps (source_name, ingredient_lines) -> new display name ("" = skip).
Namer = Callable[[str, list[str]], str]

_MODEL = os.getenv("GEMINI_VOICE_MODEL", "gemini-2.5-flash")
_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/{model}:generateContent"
)
_SYSTEM = (
    "You rename homemade DOG recipes so the title is ORIGINAL and does not copy "
    "the source title. Keep the same dish (same main ingredients). Brand voice: "
    "warm, by 'Nalla's Dad', no marketing fluff. Return ONLY the new title, "
    "3-7 words, Title Case, no quotes, clearly different wording from the source."
)


def _clean(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return re.sub(r'^["\']|["\']$', "", first.strip())


def generate_display_name(
    source_name: str,
    ingredient_lines: list[str],
    *,
    timeout: float = 60.0,
) -> str:
    """Return an original brand-voice title, or "" if unavailable.

    Yields "" (caller keeps the source name) when ``GEMINI_API_KEY`` is unset,
    the call fails, or the model just echoes the source title.
    """
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        logger.warning("GEMINI_API_KEY unset; skipping rename for %r", source_name)
        return ""
    user = (
        f"Source title: {source_name}\n"
        f"Main ingredients: {', '.join(ingredient_lines[:5])}"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": _SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 200,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    try:
        response = httpx.post(
            _ENDPOINT.format(model=_MODEL),
            params={"key": key}, json=payload, timeout=timeout,
        )
        response.raise_for_status()
        parts = response.json()["candidates"][0]["content"]["parts"]
        name = _clean("".join(p.get("text", "") for p in parts))
    except (httpx.HTTPError, KeyError, IndexError) as exc:
        logger.warning("rename failed for %r: %s", source_name, exc)
        return ""
    if not name or name.strip().lower() == source_name.strip().lower():
        return ""
    return name
