"""Auto-draft a carousel JSON for a recipe seed via Gemini.

Output schema mirrors recipe-publisher/seeds/carousels/<id>.json — a 6-slide
narrative arc (hero → pivot → ingredients → process → proof → final), each
slide with a cinematic photography prompt + an on-image text overlay.

Drafts are marked `_auto_drafted: true` so the operator knows to review/edit
before treating as final. ``ensure_carousel_json(force=True)`` (e.g. from
workers.worker_post_images) regenerates from the (possibly edited) seed.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Final

import httpx

from .seeds import RecipeSeed

logger = logging.getLogger(__name__)

CAROUSEL_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "seeds" / "carousels"
_GEMINI_MODEL: Final[str] = os.getenv("GEMINI_CAROUSEL_MODEL", "gemini-2.5-flash")
_GEMINI_ENDPOINT: Final[str] = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


_SYSTEM_PROMPT: Final[str] = """\
You author IG-carousel briefs for dogfoodandfun.com. Each brief drives both
the AI image generator (cinematic photography prompts) and a deterministic
text overlay layer.

Voice: "Nalla's Dad" — software engineer + dog owner. Warm, honest, slightly
data-driven. Never marketing-y, never medical/clinical, never generic
("delicious treat your dog will love").

NARRATIVE ARC (6 slides, in this exact order):
  1. hero        — finished product or attention-grabbing opener; bold claim
  2. pivot       — the "why this exists" / pain point / kitchen reality
  3. ingredients — overhead flat-lay of every ingredient
  4. process     — hands-on cooking moment (mid-roll, mid-pour, mid-bake)
  5. proof       — the result, dog-relevant scale or freezer/cooling shot
  6. final       — final composition, cinematic close-up of the treats

EACH SLIDE NEEDS:
  - prompt: a 80-200 word cinematic photography description. Include:
      angle, lens (50mm at f/2.8 typical), light direction (warm side-light,
      shadows, frost, etc.), surface (weathered oak, slate, etc.), depth of
      field, food photography style. Always end with: "No text, no labels,
      no watermarks, no logos, no people, no dogs."
  - overlay: { headline, subcopy }
      headline: 1-3 short lines, ALL CAPS, line breaks via \\n.
                **HARD LIMIT: max 14 characters per line including spaces and
                punctuation.** Headlines render in a chunky stroked font —
                anything longer than 14 chars gets clipped off the canvas.
                Wider letters (W, M, D) eat more space — when in doubt, go
                shorter. Punchy, data-flavored where possible.
                Examples that fit: "NALLA STOPPED" (13), "DRINKING WATER" (14),
                "I MADE" (6), "MY OWN INSTEAD" (14), "12 BISCUITS" (11).
                Examples that DO NOT fit: "WITH 3 INGREDIENTS." (19),
                "SUMMER COOL-DOWN." (17), "JUST 3 INGREDIENTS." (19).
      subcopy:  one short line, sentence case, casual.
                **HARD LIMIT: max 32 characters total.**
                Examples that fit: "for 3 days last August." (23),
                "Neither did the fan." (20), "vs $8 at the store." (19).

Output ONLY a JSON object matching this schema (no fences, no preamble):

{
  "_note": "short note about palette/mood",
  "_auto_drafted": true,
  "seed_id": "<from input>",
  "aspect_ratio": "9:16",
  "model": "nano_pro",
  "slides": [
    {"key": "hero",        "prompt": "...", "overlay": {"headline": "...", "subcopy": "..."}},
    {"key": "pivot",       "prompt": "...", "overlay": {"headline": "...", "subcopy": "..."}},
    {"key": "ingredients", "prompt": "...", "overlay": {"headline": "...", "subcopy": "..."}},
    {"key": "process",     "prompt": "...", "overlay": {"headline": "...", "subcopy": "..."}},
    {"key": "proof",       "prompt": "...", "overlay": {"headline": "...", "subcopy": "..."}},
    {"key": "final",       "prompt": "...", "overlay": {"headline": "...", "subcopy": "..."}}
  ]
}
"""


def _user_prompt(seed: RecipeSeed) -> str:
    ingredients = "\n".join(f"- {i}" for i in seed.ingredients)
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(seed.steps, 1))
    return f"""\
Draft the 6-slide carousel JSON for this recipe seed:

  seed_id: {seed.id}
  title:   {seed.title}
  category: {seed.category}
  prep / cook: {seed.prep_minutes}min / {seed.cook_minutes}min
  yield:   {seed.yield_servings}

INGREDIENTS:
{ingredients}

STEPS:
{steps}

DOG-SAFETY NOTES: {seed.dog_safety_notes}
STORAGE: {seed.storage}

Match the narrative arc described in the system prompt. Photography prompts
should clearly evoke this specific recipe — name the actual ingredients and
finished product appearance. Overlays should feel earned, never generic.
"""


_JSON_OBJECT_RE: Final[re.Pattern[str]] = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text, flags=re.IGNORECASE)
    match = _JSON_OBJECT_RE.search(cleaned)
    if not match:
        raise ValueError(f"no JSON object in response: {text[:200]!r}")
    return json.loads(match.group(0))


_REQUIRED_KEYS: Final[tuple[str, ...]] = ("hero", "pivot", "ingredients", "process", "proof", "final")
MAX_HEADLINE_LINE_CHARS: Final[int] = 14
MAX_SUBCOPY_CHARS: Final[int] = 32


def _validate(payload: dict[str, Any], expected_seed_id: str) -> None:
    if payload.get("seed_id") != expected_seed_id:
        # Tolerate — Gemini may invent a slug. Fix it before persisting.
        payload["seed_id"] = expected_seed_id
    if payload.get("aspect_ratio") != "9:16":
        payload["aspect_ratio"] = "9:16"
    if not payload.get("model"):
        payload["model"] = "nano_pro"
    slides = payload.get("slides")
    if not isinstance(slides, list) or len(slides) != 6:
        raise ValueError(f"slides must be a list of 6, got {len(slides) if isinstance(slides, list) else type(slides)}")
    keys = [s.get("key") for s in slides]
    if keys != list(_REQUIRED_KEYS):
        raise ValueError(f"slide keys must be {_REQUIRED_KEYS}, got {keys}")
    for s in slides:
        if not s.get("prompt") or not isinstance(s.get("prompt"), str):
            raise ValueError(f"slide {s.get('key')} missing prompt")
        ov = s.get("overlay") or {}
        headline = ov.get("headline") or ""
        subcopy = ov.get("subcopy") or ""
        if not headline or not subcopy:
            raise ValueError(f"slide {s.get('key')} missing overlay headline/subcopy")
        for ln in headline.split("\n"):
            if len(ln) > MAX_HEADLINE_LINE_CHARS:
                raise ValueError(
                    f"slide {s.get('key')} headline line too long "
                    f"({len(ln)} > {MAX_HEADLINE_LINE_CHARS} chars): {ln!r}"
                )
        if len(subcopy) > MAX_SUBCOPY_CHARS:
            raise ValueError(
                f"slide {s.get('key')} subcopy too long "
                f"({len(subcopy)} > {MAX_SUBCOPY_CHARS} chars): {subcopy!r}"
            )


def _call_gemini(prompt_text: str) -> dict[str, Any]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in env")
    payload = {
        "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 6144,
            "responseMimeType": "application/json",
        },
    }
    url = _GEMINI_ENDPOINT.format(model=_GEMINI_MODEL)
    r = httpx.post(url, params={"key": api_key}, json=payload, timeout=180.0)
    if r.status_code >= 400:
        raise RuntimeError(f"gemini carousel-draft HTTP {r.status_code}: {r.text[:500]}")
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError(f"gemini carousel returned no candidates: {data!r}")
    parts = cands[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    if not text.strip():
        raise RuntimeError(f"gemini carousel returned empty text; parts={parts!r}")
    return _extract_json(text)


def draft_carousel(seed: RecipeSeed) -> dict[str, Any]:
    """Call Gemini, validate, retry once with explicit feedback on overlay-length violations."""
    logger.info("gemini carousel-draft model=%s seed=%s", _GEMINI_MODEL, seed.id)
    user_prompt = _user_prompt(seed)
    out = _call_gemini(user_prompt)
    out["_auto_drafted"] = True
    try:
        _validate(out, seed.id)
        return out
    except ValueError as exc:
        logger.warning("first carousel draft failed validation, retrying with feedback: %s", exc)
        retry_prompt = (
            user_prompt
            + "\n\n---\nPREVIOUS DRAFT WAS REJECTED:\n"
            + str(exc)
            + f"\n\nThe HARD LIMITS are headline ≤{MAX_HEADLINE_LINE_CHARS} chars/line, "
            f"subcopy ≤{MAX_SUBCOPY_CHARS} chars total. Re-draft with shorter overlays."
        )
        out = _call_gemini(retry_prompt)
        out["_auto_drafted"] = True
        _validate(out, seed.id)
        return out


def ensure_carousel_json(seed: RecipeSeed, *, force: bool = False) -> Path:
    """Auto-draft + write seeds/carousels/<id>.json if missing (or always when force=True).

    Returns the path. If file already exists and force=False, returns the
    existing path without making any LLM calls.
    """
    target = CAROUSEL_DIR / f"{seed.id}.json"
    if target.exists() and not force:
        return target
    CAROUSEL_DIR.mkdir(parents=True, exist_ok=True)
    payload = draft_carousel(seed)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    logger.info("carousel JSON written: %s", target)
    return target
