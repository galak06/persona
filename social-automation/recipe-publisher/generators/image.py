"""Image generation with a pluggable provider chain.

Primary: Google Imagen 4 Fast (Gemini API).
Fallbacks, in order: Imagen 4 standard -> Pexels stock search -> static placeholder.

The generator returns a `GeneratedImage` whose `bytes_` field holds the raw image
content when produced locally. The WordPress publisher uses `bytes_` directly for
multipart upload (no re-download), so `url` is informational except when we fall
through to Pexels (which gives us a real hosted URL) or to the static placeholder.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_IMAGEN_PREDICT_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:predict"
)
_GEMINI_GENCONTENT_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
_IMAGEN_FAST = "imagen-4.0-fast-generate-001"
_IMAGEN_STANDARD = "imagen-4.0-generate-001"
_NANO_PRO = "gemini-3-pro-image-preview"

_STYLE_SUFFIX = (
    " Natural food photography, overhead angle, warm afternoon window light, "
    "soft shadows, home-kitchen feel on a weathered wooden surface, shallow depth of field."
)
_NEGATIVES = (
    " Do not include: any text, labels, watermarks, logos, brand marks, hands, "
    "people, dogs, cats, cutlery, spoons, forks, knives, straws, or packaging."
)


@dataclass
class GeneratedImage:
    url: str
    alt_text: str
    provider: str
    bytes_: bytes | None = None
    content_type: str = "image/png"


class ImageGenerationError(RuntimeError):
    pass


def generate_image(brief: str, *, alt_hint: str) -> GeneratedImage:
    """Generate an image for `brief`, walking the fallback chain until one succeeds.

    The chain honors `IMAGE_PROVIDER` as a hard override:
      - "imagen_fast" (default), "imagen_standard", "pexels", "fallback"
    When unset, runs the full chain: imagen_fast -> imagen_standard -> pexels -> fallback.
    """
    alt_text = _derive_alt(alt_hint, brief)
    override = os.getenv("IMAGE_PROVIDER", "").lower().strip()

    if override:
        chain = [override]
    else:
        chain = ["imagen_fast", "imagen_standard", "pexels", "fallback"]

    errors: list[str] = []
    for step in chain:
        try:
            if step == "imagen_fast":
                img = _generate_imagen(brief, model=_IMAGEN_FAST, provider="imagen_fast")
            elif step == "imagen_standard":
                img = _generate_imagen(brief, model=_IMAGEN_STANDARD, provider="imagen_standard")
            elif step == "nano_pro":
                img = _generate_nano_pro(brief)
            elif step == "pexels":
                img = _generate_pexels(_pexels_query_from(alt_hint))
            elif step == "fallback":
                img = _generate_fallback()
            else:
                raise ImageGenerationError(f"unknown IMAGE_PROVIDER={step!r}")
            img.alt_text = alt_text
            logger.info("image via provider=%s bytes=%s", img.provider, len(img.bytes_ or b""))
            return img
        except ImageGenerationError as e:
            logger.warning("image provider %s failed: %s", step, e)
            errors.append(f"{step}: {e}")
            continue

    raise ImageGenerationError("all providers failed: " + "; ".join(errors))


def _derive_alt(title: str, brief: str) -> str:
    """Short, descriptive, keyword-bearing alt text. Never empty, never 'image'."""
    first_sentence = brief.split(".", 1)[0].strip()
    if len(first_sentence) > 90:
        first_sentence = first_sentence[:87].rsplit(" ", 1)[0] + "…"
    title = title.strip() or "Dog-safe recipe"
    return f"{title} — {first_sentence}" if first_sentence else title


def _pexels_query_from(alt_hint: str) -> str:
    """Strip 'Dog-Safe ' / 'Recipe' / 'for Dogs' so Pexels actually returns food photos."""
    q = re.sub(r"(?i)\b(dog[- ]safe|for dogs|recipe)\b", "", alt_hint).strip()
    return q or "homemade dog food"


# ---------- Imagen (Gemini API) ----------


def _generate_imagen(brief: str, *, model: str, provider: str, timeout: float = 60.0) -> GeneratedImage:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise ImageGenerationError("GEMINI_API_KEY not set")

    prompt = brief.strip() + _STYLE_SUFFIX + _NEGATIVES
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


# ---------- Nano Banana Pro (Gemini 3 Pro Image) ----------


def _generate_nano_pro(
    brief: str, *, aspect_ratio: str = "1:1", timeout: float = 180.0
) -> GeneratedImage:
    """Gemini 3 Pro Image Preview — stronger photography + text adherence than Imagen.

    Used for IG carousel slides where quality and prompt adherence matter most.
    Does NOT append _STYLE_SUFFIX/_NEGATIVES — the caller supplies a fully-detailed
    cinematic prompt (carousel slides each have their own curated prompt in the seed).
    """
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise ImageGenerationError("GEMINI_API_KEY not set")

    url = _GEMINI_GENCONTENT_ENDPOINT.format(model=_NANO_PRO)
    payload = {
        "contents": [{"parts": [{"text": brief}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": aspect_ratio},
        },
    }
    try:
        r = httpx.post(url, params={"key": key}, json=payload, timeout=timeout)
    except httpx.HTTPError as e:
        raise ImageGenerationError(f"nano_pro request error: {e}") from e
    if r.status_code >= 400:
        raise ImageGenerationError(f"nano_pro HTTP {r.status_code}: {r.text[:300]}")

    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise ImageGenerationError(f"nano_pro: no candidates in {data!r}")
    parts = cands[0].get("content", {}).get("parts", [])
    for p in parts:
        inline = p.get("inlineData") or p.get("inline_data") or {}
        if inline.get("data"):
            raw = base64.b64decode(inline["data"])
            mime = inline.get("mimeType", "image/jpeg")
            return GeneratedImage(
                url=f"nano_pro://{_NANO_PRO}",
                alt_text="",
                provider="nano_pro",
                bytes_=raw,
                content_type=mime,
            )
    raise ImageGenerationError(f"nano_pro: no image bytes in parts {parts!r}")


# ---------- Pexels ----------


def _generate_pexels(query: str, timeout: float = 30.0) -> GeneratedImage:
    key = os.getenv("PEXELS_API_KEY")
    if not key:
        raise ImageGenerationError("PEXELS_API_KEY not set")
    try:
        r = httpx.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "orientation": "landscape", "per_page": 3, "size": "large"},
            headers={"Authorization": key},
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise ImageGenerationError(f"pexels request error: {e}") from e
    if r.status_code >= 400:
        raise ImageGenerationError(f"pexels HTTP {r.status_code}: {r.text[:200]}")
    photos = r.json().get("photos") or []
    if not photos:
        raise ImageGenerationError(f"pexels: no results for query={query!r}")
    photo = photos[0]
    src = photo["src"].get("large2x") or photo["src"].get("large") or photo["src"]["original"]
    try:
        img = httpx.get(src, timeout=timeout)
        img.raise_for_status()
    except httpx.HTTPError as e:
        raise ImageGenerationError(f"pexels download error: {e}") from e
    return GeneratedImage(
        url=src,
        alt_text="",
        provider="pexels",
        bytes_=img.content,
        content_type=img.headers.get("Content-Type", "image/jpeg"),
    )


# ---------- Static placeholder ----------


def _generate_fallback() -> GeneratedImage:
    url = os.getenv("FALLBACK_IMAGE_URL", "")
    if not url:
        raise ImageGenerationError("FALLBACK_IMAGE_URL not set")
    try:
        r = httpx.get(url, timeout=30.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ImageGenerationError(f"fallback download error: {e}") from e
    return GeneratedImage(
        url=url,
        alt_text="",
        provider="fallback",
        bytes_=r.content,
        content_type=r.headers.get("Content-Type", "image/jpeg"),
    )
