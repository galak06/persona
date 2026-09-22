"""Plan a 4-slide carousel + caption for any brand.

Everything the model is told about the brand comes from `BrandProfile`.
There is no niche, species, or persona baked into this module — point it at
a coffee brand's config and it plans coffee slides.

`offline=True` skips the model entirely and returns a deterministic
template plan, so a fresh clone renders real output with no API key.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
from dataclasses import dataclass
from typing import Any

import httpx

from studio.brand import BrandProfile

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_DEFAULT_MODEL = "gemini-3.6-flash"

SLIDE_KEYS: tuple[str, ...] = ("hero", "info_1", "info_2", "final")


@dataclass(frozen=True)
class SlidePlan:
    key: str
    headline: str
    subcopy: str
    image_query: str
    image_brief: str


@dataclass(frozen=True)
class CarouselPlan:
    slides: list[SlidePlan]
    caption: str


class PlanningError(RuntimeError):
    """Raised when the model could not produce a usable plan."""


def _prompt(topic: str, brand: BrandProfile) -> str:
    mascot_line = (
        f"- Mention {brand.mascot_clause} once in the caption, naturally.\n"
        f"- Never invent physical details about the mascot that you were not given.\n"
        if brand.mascot_clause
        else "- This brand has no mascot. Do not invent one.\n"
    )
    return textwrap.dedent(f"""\
        You are designing a 4-slide Instagram carousel.

        Brand:     {brand.name}
        Niche:     {brand.niche or "general interest"}
        Audience:  {brand.audience or "a general audience"}
        Voice:     {brand.persona or "warm, personal, first-person"}
        Topic:     "{topic}"

        Return ONLY valid JSON — one object with keys "caption" and "slides".
        "slides" is a list of exactly 4 objects with keys in this order:
        {", ".join(SLIDE_KEYS)}.

        Each slide object has:
          "key":         one of {", ".join(SLIDE_KEYS)}
          "headline":    ALL CAPS hook, max 14 chars per line, split with \\n, max 2 lines
          "subcopy":     one supporting line, max 60 characters
          "image_query": a 2-4 word stock-photo search
          "image_brief": a photorealistic image description — no text, logos, or watermarks in frame

        Slide roles: hero = the hook. info_1 and info_2 = the two most useful
        specifics. final = the payoff plus a save/bookmark nudge.

        Caption rules:
        - 80-140 words, first person, in the brand voice above
        - Ends with a genuine question to the reader
        - Last line is 6-8 relevant hashtags
        {mascot_line}- No sales language, no "click here", no medical or absolute claims

        No markdown, no code fences — pure JSON only.
    """)


def _extract_json_obj(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text, flags=re.IGNORECASE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise PlanningError(f"no JSON object in model response: {text[:300]!r}")
    try:
        obj: dict[str, Any] = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as e:
        raise PlanningError(f"malformed JSON from model: {e}") from e
    return obj


def _coerce(obj: dict[str, Any], topic: str, brand: BrandProfile) -> CarouselPlan:
    raw_slides = obj.get("slides") or []
    if len(raw_slides) < len(SLIDE_KEYS):
        raise PlanningError(f"expected {len(SLIDE_KEYS)} slides, got {len(raw_slides)}")
    slides = [
        SlidePlan(
            key=str(s.get("key") or SLIDE_KEYS[i]),
            headline=str(s.get("headline") or topic.upper()),
            subcopy=str(s.get("subcopy") or "")[:60],
            image_query=str(s.get("image_query") or topic),
            image_brief=str(s.get("image_brief") or topic),
        )
        for i, s in enumerate(raw_slides[: len(SLIDE_KEYS)])
    ]
    caption = str(obj.get("caption") or "").strip()
    return CarouselPlan(slides=slides, caption=caption or _fallback_caption(topic, brand))


def _fallback_caption(topic: str, brand: BrandProfile) -> str:
    tag = re.sub(r"[^a-z0-9]", "", brand.name.lower()) or "persona"
    return f"{topic}\n\nWhat's worked for you?\n\n#{tag}"


def _offline_plan(topic: str, brand: BrandProfile) -> CarouselPlan:
    """A deterministic plan used when no model is configured."""

    def head(text: str, budget: int = 34) -> str:
        """Uppercase hook, trimmed at a word boundary.

        The overlay renderer re-wraps to fit the frame, so this only bounds
        the total length — it must never drop trailing words, or the hook
        loses the word it turns on ("...TASTES" without "BITTER").
        """
        words = text.upper().split()
        out: list[str] = []
        for w in words:
            if out and len(" ".join([*out, w])) > budget:
                break
            out.append(w)
        return " ".join(out or words[:1])

    roles = (
        (topic, "Here's the short version.", topic),
        ("What matters", "The first thing worth knowing.", f"{topic} detail"),
        ("What helps", "The one change with the biggest payoff.", f"{topic} closeup"),
        ("Save this", "Keep it handy for later.", brand.niche or topic),
    )
    slides = [
        SlidePlan(
            key=key,
            headline=head(h),
            subcopy=sub,
            image_query=q,
            image_brief=f"Photorealistic lifestyle image: {q}. No text or logos in frame.",
        )
        for key, (h, sub, q) in zip(SLIDE_KEYS, roles, strict=True)
    ]
    return CarouselPlan(slides=slides, caption=_fallback_caption(topic, brand))


def plan_carousel(
    topic: str,
    brand: BrandProfile,
    *,
    offline: bool = False,
    model: str = _DEFAULT_MODEL,
    timeout: float = 60.0,
) -> CarouselPlan:
    """Plan slides + caption for `topic`, in `brand`'s voice."""
    if not topic.strip():
        raise ValueError("topic must not be empty")

    api_key = os.environ.get("GEMINI_API_KEY")
    if offline or not api_key:
        return _offline_plan(topic, brand)

    payload = {
        "contents": [{"role": "user", "parts": [{"text": _prompt(topic, brand)}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096},
    }
    try:
        # Key travels in a header, never the query string — a URL-embedded key
        # ends up in access logs and error traces.
        r = httpx.post(
            _GEMINI_URL.format(model=model),
            headers={"x-goog-api-key": api_key},
            json=payload,
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise PlanningError(f"model request failed: {e}") from e
    if r.status_code >= 400:
        raise PlanningError(f"model HTTP {r.status_code}: {r.text[:300]}")

    parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    raw = next((p["text"] for p in parts if p.get("text")), "")
    return _coerce(_extract_json_obj(raw), topic, brand)
