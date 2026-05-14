"""Draft singable lyrics for a recipe via Gemini.

The lyrics narrate the recipe story in a way that mirrors the carousel
flow — opening hook, problem, ingredients, process, payoff. Output is a
markdown file the operator feeds into Suno (or another music tool) to
generate the audio.mp3 that lands in the campaign folder.

Reference style (from existing campaigns/published/blueberry-yogurt-frozen-bites/lyrics.md):
    Sun is melting on the pavement
    Need a moment of salvation
    Stir some yogurt and some berries
    Frozen bites, my sweet reprieve
    ...

~10-14 lines, 6-9 syllables each, free meter, mostly end-rhymed in pairs.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Final

import httpx

from .seeds import RecipeSeed

logger = logging.getLogger(__name__)

_GEMINI_MODEL: Final[str] = os.getenv("GEMINI_LYRICS_MODEL", "gemini-2.5-flash")
_GEMINI_ENDPOINT: Final[str] = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


_SYSTEM_PROMPT: Final[str] = """\
You write short singable lyrics for IG-Reel music tracks about homemade dog
recipes. The voice is warm and personal — the dog owner ("Nalla's Dad") in
the kitchen, narrating what they're making for their dog.

Hard rules:
- 10–14 lines, 6–9 syllables each.
- Free meter, mostly end-rhymed pairs (AABB or ABAB acceptable).
- Narrative arc that mirrors a 6-slide carousel:
    opening hook → problem / pivot → ingredients → process → payoff → outro
- Concrete sensory details from THIS recipe: name actual ingredients,
  describe smells, sounds, textures, kitchen moments.
- Mention the dog (or "pup") at least once.
- NEVER mention brands, marketing claims, "best", "favorite", "amazing".
- NEVER medical/clinical language ("vet-approved", "clinically proven").
- NO chorus repetition unless it serves the story.

Output: ONLY the lyrics body. Plain text. No title line, no markdown headings,
no commentary, no explanation. The first line is the first lyric line.
"""


def _user_prompt(seed: RecipeSeed) -> str:
    ingredient_lines = "\n".join(f"- {i}" for i in seed.ingredients[:8])
    step_lines = "\n".join(f"{i}. {s}" for i, s in enumerate(seed.steps[:6], 1))
    return f"""\
Recipe: {seed.title}
Category: {seed.category}
Prep / cook: {seed.prep_minutes}min / {seed.cook_minutes}min

INGREDIENTS:
{ingredient_lines}

STEPS:
{step_lines}

Write the lyrics now. 10–14 lines. Output the lines and nothing else.
"""


_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"```[a-zA-Z]*\s*|\s*```", re.IGNORECASE)


def draft_lyrics(seed: RecipeSeed) -> str:
    """Return the lyrics body as plain text (markdown-safe)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in env")

    payload = {
        "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": _user_prompt(seed)}]}],
        "generationConfig": {
            "temperature": 0.85,
            "maxOutputTokens": 1024,
        },
    }

    url = _GEMINI_ENDPOINT.format(model=_GEMINI_MODEL)
    logger.info("gemini lyrics-draft model=%s seed=%s", _GEMINI_MODEL, seed.id)
    r = httpx.post(url, params={"key": api_key}, json=payload, timeout=120.0)
    if r.status_code >= 400:
        raise RuntimeError(f"gemini lyrics HTTP {r.status_code}: {r.text[:500]}")

    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError(f"gemini lyrics returned no candidates: {data!r}")
    parts = cands[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    cleaned = _FENCE_RE.sub("", text).strip()
    # Drop any leading title line ("# Lyrics" / "**Lyrics**")
    lines = [ln for ln in cleaned.splitlines()]
    while lines and (lines[0].lstrip().startswith(("#", "*", "_")) or not lines[0].strip()):
        lines.pop(0)
    return "\n".join(ln.rstrip() for ln in lines if ln.strip()).strip()


def render_lyrics_md(seed: RecipeSeed, lyrics_body: str) -> str:
    """Wrap the lyrics body in the standard markdown header block."""
    return (
        f"# {seed.title} — Lyrics\n\n"
        f"<!-- AUTO-DRAFTED — review/edit before feeding to your music tool. -->\n\n"
        f"{lyrics_body}\n"
    )
