"""Content-generation helpers for wordpress_ideas.py — split for line-count compliance."""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import httpx

from generators.image import (
    GeneratedImage,
    ImageGenerationError,
    _IMAGEN_FAST,
    _IMAGEN_PREDICT_ENDPOINT,
    _IMAGEN_STANDARD,
)

logger = logging.getLogger(__name__)

# Editorial style for idea posts — explicitly no food props.
# Positive framing (describe what we want) + hard negation for the specific
# objects that keep leaking into generated images via the recipe pipeline.
_IDEA_IMAGE_STYLE = (
    "Nalla a fluffy tan-and-black shepherd mix dog. "
    "Photorealistic photography, natural lighting, authentic home or outdoor setting. "
    "IMPORTANT: absolutely no dog food, no dog treats, no bowls, no cutting boards, "
    "no parchment paper, no kitchen counters with food. "
    "Focus on the scene described in the brief."
)
_NEGATIVES = " No text, labels, watermarks, logos, or packaging."


def load_nalla_facts() -> str:
    brand_dir = os.environ.get("BRAND_DIR", "")
    if not brand_dir:
        return ""
    p = Path(brand_dir) / "data" / "config" / "nalla_facts.md"
    return p.read_text() if p.exists() else ""


def call_gemini(idea: dict, enrichment: dict) -> str:
    """Generate blog post markdown via Gemini API (direct call, 4096-token output)."""
    api_key = os.environ["GEMINI_API_KEY"]
    model = os.getenv("GEMINI_CONTENT_MODEL", "gemini-2.5-flash")
    nalla_facts = load_nalla_facts()

    topic = idea.get("topic", "")
    e = enrichment
    suggested_title = e.get("suggested_title") or topic
    primary_keyword = e.get("primary_keyword") or idea.get("target_keyword") or ""
    secondary_keywords: list[str] = e.get("secondary_keywords") or []
    nalla_angle = e.get("nalla_angle") or idea.get("nalla_context") or ""
    post_goal = e.get("post_goal") or idea.get("post_goal") or ""
    input_ = idea.get("input") or ""
    outline = e.get("outline") or ""
    people_also_ask: list[str] = e.get("people_also_ask") or []
    internal_links: list[dict] = e.get("internal_links") or []

    faq_block = (
        "Use these as FAQ section headers (verbatim):\n"
        + "\n".join(f"- {q}" for q in people_also_ask)
        if people_also_ask
        else ""
    )
    links_block = (
        "Weave in these internal links naturally:\n"
        + "\n".join(
            f"- [{il['anchor_text']}]({il.get('url', '#')})" for il in internal_links
        )
        if internal_links
        else ""
    )
    outline_block = f"Follow this outline:\n{outline}" if outline else ""

    user_parts = [
        f"Write a 1500-2500 word blog post in markdown.\n\n"
        f"Title: {suggested_title}\n"
        f"Primary keyword (use 3-5x naturally): {primary_keyword}\n"
        f"Secondary keywords: {', '.join(secondary_keywords)}\n"
        f"Personal Nalla angle: {nalla_angle}\n"
        f"Post goal / CTA: {post_goal}\n"
        f"Additional context: {input_}",
        outline_block,
        faq_block,
        links_block,
        "Rules:\n"
        "- Markdown only, no preamble, no code fences\n"
        "- Mention Nalla (fluffy shepherd mix) naturally 2-3 times\n"
        "- End with a short conclusion + CTA aligned to the post goal\n"
        "- Include a ## Frequently Asked Questions section using the FAQ headers above",
    ]
    user_prompt = "\n\n".join(p for p in user_parts if p)

    payload = {
        "system_instruction": {
            "parts": [{"text": (
                "You are a dog-owner lifestyle blogger writing warm, first-person posts "
                f"for your-brand.com.\nBrand voice and persona:\n{nalla_facts}"
            )}]
        },
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 4096,
            "temperature": 0.7,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    )
    r = httpx.post(endpoint, params={"key": api_key}, json=payload, timeout=120.0)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def generate_idea_image(brief: str, alt_hint: str = "") -> GeneratedImage:
    """Generate a topic-appropriate image — never appends recipe food-style suffix.

    Tries Imagen 4 Fast, then Imagen 4 Standard. Never calls generate_image() which
    would append _STYLE_SUFFIX (dog-treat scene, bowl, cutting board). Returns a
    placeholder GeneratedImage (url="placeholder", bytes_=b"") on total failure so
    the caller can skip the image upload gracefully.
    """
    key = os.environ.get("GEMINI_API_KEY", "")
    alt_text = alt_hint or brief[:80]
    full_prompt = f"{brief}. {_IDEA_IMAGE_STYLE}{_NEGATIVES}"

    for model, provider in [
        (_IMAGEN_FAST, "imagen_fast"),
        (_IMAGEN_STANDARD, "imagen_standard"),
    ]:
        if not key:
            break
        try:
            img = _call_imagen(full_prompt, model=model, provider=provider, key=key)
            img.alt_text = alt_text
            logger.info(
                "idea image via provider=%s bytes=%d", provider, len(img.bytes_ or b"")
            )
            return img
        except ImageGenerationError as exc:
            logger.warning("idea image provider %s failed: %s", provider, exc)

    logger.warning("all idea image providers failed; returning placeholder")
    return GeneratedImage(
        url="placeholder",
        alt_text=alt_text,
        provider="none",
        bytes_=b"",
        content_type="image/png",
    )


def _call_imagen(
    prompt: str, *, model: str, provider: str, key: str, timeout: float = 60.0
) -> GeneratedImage:
    """Call the Gemini Imagen predict endpoint and return raw image bytes."""
    url = _IMAGEN_PREDICT_ENDPOINT.format(model=model)
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": "16:9",
            "personGeneration": "dont_allow",
        },
    }
    try:
        r = httpx.post(url, params={"key": key}, json=payload, timeout=timeout)
    except httpx.HTTPError as e:
        raise ImageGenerationError(f"imagen request error: {e}") from e
    if r.status_code >= 400:
        raise ImageGenerationError(f"imagen HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    preds = data.get("predictions") or []
    if not preds:
        raise ImageGenerationError(f"imagen returned no predictions: {data!r}")
    first = preds[0]
    b64 = first.get("bytesBase64Encoded", "")
    if not b64:
        raise ImageGenerationError(f"imagen missing image bytes: {first!r}")
    mime = first.get("mimeType", "image/png")
    raw = base64.b64decode(b64)
    return GeneratedImage(
        url=f"imagen://{model}",
        alt_text="",  # populated by caller
        provider=provider,
        bytes_=raw,
        content_type=mime,
    )
