"""Turn an approved candidate into a full structured seed JSON via Gemini.

No web search at this stage — this is the LLM filling in a known schema.
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
from typing import Any, Final

import httpx

from .research import Candidate
from .schema import ALLOWED_CATEGORIES

logger = logging.getLogger(__name__)

_GEMINI_MODEL: Final[str] = os.getenv("GEMINI_ENRICHER_MODEL", "gemini-2.5-flash")
_GEMINI_ENDPOINT: Final[str] = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


_SYSTEM_PROMPT: Final[str] = f"""\
You generate structured recipe seeds for your-brand.com. The seed is the
factual backbone of a recipe (ingredients, steps, safety) — voice / Nalla
stories are added later by a separate drafter, so DO NOT add prose narratives
or marketing language here.

DOG-SAFETY RULES (non-negotiable):
- NEVER include xylitol (always note "xylitol-free" for peanut butter).
- NEVER include chocolate, cocoa, raisins, grapes, macadamias, alcohol,
  caffeine, coffee, or nutmeg.
- Garlic and onion: avoid entirely OR very small cooked amounts ONLY if
  recipe is for a multi-day batch with low daily exposure — note safety.
- Sodium: avoid added salt where possible.

OUTPUT FORMAT (strict):
- id: lowercase-kebab-case, ≤40 chars (e.g. "summer-watermelon-cubes").
- title: human-readable, ≤70 chars.
- topic_keywords: 4-8 short phrases.
- category: must be one of {sorted(ALLOWED_CATEGORIES)}.
- prep_minutes / cook_minutes: integers 0..240.
- yield_servings: human-readable ("makes ~24 cubes" / "feeds 1 medium dog 4 days").
- tags: 3-6 hyphenated lowercase tags.
- ingredients: 4-10 items. Each item with measurement AND grams in parens —
  e.g. "1 cup (240 ml) bone broth, no salt added, no onion".
- steps: 5-10 numbered actions, each a complete sentence.
- dog_safety_notes: 1-3 sentences naming SPECIFIC allergen warnings for the
  ingredients used.
- storage: 1 sentence on fridge/freezer durations.
- portion_guide: object with small / medium / large keys, each a short
  feeding guideline.
- source_attribution: 1-2 sentences citing GENERIC sources (AKC, AAFCO,
  veterinary nutrition guides). Don't fabricate specific URLs.
"""


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
        "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
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
