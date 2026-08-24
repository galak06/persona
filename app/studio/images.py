"""Slide imagery with a graceful provider chain.

Order: Gemini image generation → Pexels stock → an offline generated
placeholder. The placeholder is what lets `cli.py demo` produce real output
on a fresh clone with no API keys at all, which is the difference between a
repo someone tries and a repo someone bounces off.
"""

from __future__ import annotations

import base64
import colorsys
import hashlib
import io
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from PIL import Image, ImageDraw, ImageFilter

logger = logging.getLogger(__name__)

_GEMINI_IMAGE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
_IMAGE_MODEL = "gemini-3-pro-image-preview"
_PEXELS_URL = "https://api.pexels.com/v1/search"


class ImageUnavailableError(RuntimeError):
    """One provider could not supply an image; the chain moves on."""


@dataclass
class SlideImage:
    """A generated slide image plus the provider that produced it."""

    bytes_: bytes
    provider: str
    content_type: str = "image/jpeg"
    alt_text: str = ""


def _hue_pair(seed: str) -> tuple[float, float]:
    """Two related hues derived from `seed`, stable across runs."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    base = digest[0] / 255.0
    return base, (base + 0.12) % 1.0


def generate_placeholder(seed: str, size: int = 1080) -> SlideImage:
    """Render a deterministic gradient slide — no network, no API key.

    The same seed always yields the same image, so demo output is
    reproducible and README screenshots stay stable.
    """
    h1, h2 = _hue_pair(seed)
    top = tuple(int(c * 255) for c in colorsys.hls_to_rgb(h1, 0.42, 0.46))
    bottom = tuple(int(c * 255) for c in colorsys.hls_to_rgb(h2, 0.16, 0.40))

    img = Image.new("RGB", (size, size), top)
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(size - 1, 1)
        draw.line(
            [(0, y), (size, y)],
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
        )

    # A soft off-centre bloom keeps the frame from reading as a flat CSS
    # gradient once the headline band sits on top of it.
    bloom = Image.new("L", (size, size), 0)
    ImageDraw.Draw(bloom).ellipse(
        [int(size * 0.12), int(size * -0.10), int(size * 0.88), int(size * 0.66)],
        fill=70,
    )
    bloom = bloom.filter(ImageFilter.GaussianBlur(size * 0.16))
    img = Image.composite(Image.new("RGB", (size, size), (255, 255, 255)), img, bloom)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return SlideImage(bytes_=buf.getvalue(), provider="placeholder")


def _gemini_image(brief: str, *, timeout: float = 180.0) -> SlideImage:
    """Generate a square slide image via Gemini.

    The key travels in a header. The provider helper in `generators/image.py`
    passes it as a `?key=` query parameter, which httpx then writes into the
    request log at INFO level — running the CLI with `-v` would print the
    caller's API key to stdout. This module does not inherit that.
    """
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ImageUnavailableError("GEMINI_API_KEY not set")

    url = _GEMINI_IMAGE_URL.format(model=_IMAGE_MODEL)
    payload = {
        "contents": [{"parts": [{"text": brief}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "1:1"},
        },
    }
    try:
        r = httpx.post(url, headers={"x-goog-api-key": key}, json=payload, timeout=timeout)
    except httpx.HTTPError as e:
        raise ImageUnavailableError(f"gemini request failed: {e}") from e
    if r.status_code >= 400:
        raise ImageUnavailableError(f"gemini HTTP {r.status_code}: {r.text[:200]}")

    for part in (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", []):
        inline = part.get("inlineData") or part.get("inline_data") or {}
        if inline.get("data"):
            return SlideImage(
                bytes_=base64.b64decode(inline["data"]),
                provider="gemini",
                content_type=inline.get("mimeType", "image/jpeg"),
            )
    raise ImageUnavailableError("gemini returned no image data")


def _pexels_image(query: str, *, timeout: float = 30.0) -> SlideImage:
    """Fetch a square stock photo from Pexels."""
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        raise ImageUnavailableError("PEXELS_API_KEY not set")
    try:
        r = httpx.get(
            _PEXELS_URL,
            headers={"Authorization": key},
            params={"query": query, "per_page": 1, "orientation": "square"},
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise ImageUnavailableError(f"pexels request failed: {e}") from e
    if r.status_code >= 400:
        raise ImageUnavailableError(f"pexels HTTP {r.status_code}")

    photos = r.json().get("photos") or []
    if not photos:
        raise ImageUnavailableError(f"pexels had no results for {query!r}")
    src = photos[0].get("src", {})
    href = src.get("large") or src.get("original")
    if not href:
        raise ImageUnavailableError("pexels result had no usable size")
    try:
        img = httpx.get(href, timeout=timeout)
        img.raise_for_status()
    except httpx.HTTPError as e:
        raise ImageUnavailableError(f"pexels download failed: {e}") from e
    return SlideImage(
        bytes_=img.content,
        provider="pexels",
        content_type=img.headers.get("Content-Type", "image/jpeg"),
        alt_text=photos[0].get("alt", "") or "",
    )


def generate_slide_image(
    *,
    brief: str,
    query: str,
    offline: bool = False,
) -> SlideImage:
    """Best available image for one slide, falling back rather than failing."""
    if offline:
        return generate_placeholder(query or brief)

    providers: tuple[tuple[str, Callable[[], SlideImage]], ...] = (
        ("gemini", lambda: _gemini_image(brief)),
        ("pexels", lambda: _pexels_image(query)),
    )
    for label, call in providers:
        try:
            return call()
        except ImageUnavailableError as e:
            logger.warning("image provider %s unavailable (%s) - falling back", label, e)

    logger.warning("no image provider available - using offline placeholder")
    return generate_placeholder(query or brief)
