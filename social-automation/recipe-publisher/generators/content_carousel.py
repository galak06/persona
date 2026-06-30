"""Content-idea carousel: topic + notes → 4 generated images with text overlays.

Gemini plans 4 slides (headline, subcopy, image_brief) and an IG caption.
The image provider chain (nano_pro → pexels) generates each image.
PIL text_overlay renders the deterministic overlay on top.

Entry point:
    slides, caption = generate_content_slides(idea)
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .image import GeneratedImage, ImageGenerationError, _generate_nano_pro, _generate_pexels
from .text_overlay import OverlaySpec, apply_follow_badge, apply_overlay, apply_site_cta_ribbon

logger = logging.getLogger(__name__)

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    "/gemini-2.5-flash:generateContent"
)

_PLAN_PROMPT = """\
You are designing a 4-slide Instagram carousel for a dog-lifestyle brand.

Topic: "{topic}"
Category: {category}
Trending signal: {signal}

Return ONLY valid JSON — a single object with two keys:
{{
  "caption": "<IG caption, 80-140 words, warm personal tone, ends with an engagement question, 6-8 hashtags on the last line>",
  "slides": [
    {{
      "key": "hero",
      "headline": "HOOK IN\n2 LINES",
      "subcopy": "One compelling sentence, max 65 chars",
      "image_query": "pexels search 2-4 words",
      "image_brief": "Photorealistic image description, no text in frame"
    }},
    {{
      "key": "info_1",
      "headline": "FIRST POINT\nHERE",
      "subcopy": "Supporting detail, max 65 chars",
      "image_query": "pexels search 2-4 words",
      "image_brief": "Photorealistic image description, no text in frame"
    }},
    {{
      "key": "info_2",
      "headline": "SECOND POINT\nHERE",
      "subcopy": "Supporting detail, max 65 chars",
      "image_query": "pexels search 2-4 words",
      "image_brief": "Photorealistic image description, no text in frame"
    }},
    {{
      "key": "final",
      "headline": "KEEP YOUR\nDOG SAFE",
      "subcopy": "One CTA sentence — bookmark this!, max 65 chars",
      "image_query": "dog garden safe happy",
      "image_brief": "Happy dog in a garden, warm sunshine, photorealistic"
    }}
  ]
}}

Rules:
- headline is ALL CAPS, max 14 chars per line, split with \\n
- subcopy: max 60 chars — SHORT, fits on one line
- Use dog-owner friendly language, reference Nalla once in the caption
- Nalla is a black-and-tan shepherd mix with medium-length fluffy fur (NOT a Golden Retriever)
- image_brief: photorealistic, lifestyle, no text, no watermarks; when showing a dog use "black and tan shepherd mix dog with medium fluffy fur"
- image_query: when showing a dog use "shepherd mix dog" not "golden retriever"
- No markdown, no code fences — pure JSON only
"""


@dataclass
class SlideSpec:
    key: str
    headline: str
    subcopy: str
    image_query: str
    image_brief: str


def plan_slides(
    topic: str,
    category: str,
    search_signal: str | None,
) -> tuple[list[SlideSpec], str]:
    """Call Gemini to produce 4 slide specs + IG caption. Returns (slides, caption)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    prompt = _PLAN_PROMPT.format(
        topic=topic,
        category=category,
        signal=search_signal or "current seasonal trends",
    )
    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096},
    }
    r = httpx.post(_GEMINI_URL, params={"key": api_key}, json=payload, timeout=60.0)
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")

    parts = (
        (r.json().get("candidates") or [{}])[0]
        .get("content", {})
        .get("parts", [])
    )
    raw = next((p["text"] for p in parts if p.get("text")), "")
    obj = _extract_json_obj(raw)

    slides = [
        SlideSpec(
            key=s["key"],
            headline=s["headline"],
            subcopy=s["subcopy"],
            image_query=s["image_query"],
            image_brief=s["image_brief"],
        )
        for s in obj["slides"]
    ]
    caption: str = obj.get("caption", f"🐾 {topic}\n\n#doghealth #doglife #dogfoodandfun")
    return slides, caption


def _extract_json_obj(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text, flags=re.IGNORECASE).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in Gemini response: {text[:300]!r}")
    return json.loads(cleaned[start : end + 1])


def _generate_slide_image(spec: SlideSpec) -> GeneratedImage:
    """Try Gemini image gen first, fall back to Pexels stock."""
    try:
        return _generate_nano_pro(spec.image_brief, aspect_ratio="1:1")
    except ImageGenerationError as e:
        logger.warning("nano_pro failed for slide %s (%s) — trying pexels", spec.key, e)
    return _generate_pexels(spec.image_query)


def generate_content_slides(
    idea: dict[str, Any],
    *,
    ig_handle: str = "@dogfoodandfun",
    site_cta: str = "FULL GUIDE  →  DOGFOODANDFUN.COM",
) -> tuple[list[GeneratedImage], str]:
    """Plan + generate 4 carousel slides for a content idea.

    Returns (slides, ig_caption) where each slide has `.bytes_` populated.
    """
    topic: str = idea.get("topic") or ""
    category: str = idea.get("category") or ""
    search_signal: str | None = idea.get("input")

    logger.info("planning slides: topic=%r category=%s", topic, category)
    slide_specs, caption = plan_slides(topic, category, search_signal)

    out: list[GeneratedImage] = []
    for spec in slide_specs:
        logger.info("generating slide key=%s query=%r", spec.key, spec.image_query)
        img = _generate_slide_image(spec)

        overlaid = apply_overlay(
            img.bytes_ or b"",
            OverlaySpec(headline=spec.headline, subcopy=spec.subcopy),
            headline_y_pct=0.72,
            band_top_pct=0.58,
        )

        if spec.key == "hero":
            overlaid = apply_follow_badge(overlaid, handle=ig_handle)
        if spec.key == "final":
            overlaid = apply_site_cta_ribbon(overlaid, cta_text=site_cta)

        img.bytes_ = overlaid
        img.content_type = "image/jpeg"
        img.alt_text = f"{topic} — {spec.key.replace('_', ' ')}"
        out.append(img)

    return out, caption
