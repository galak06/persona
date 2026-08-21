"""The image providers `lib.crew.wp_image` calls, and what they return.

Split out of that module -- which sat at exactly the 300-line ceiling -- when
the nano_pro call had to learn to carry MORE THAN ONE reference photo: a
scene reference plus the photo that anchors the brand's own mascot (see
`lib.crew.reference_mascot` for why one photo was never enough).

The two halves are different jobs. This one speaks HTTP to a single provider
and raises `ImageGenerationError` when that provider fails; `wp_image` owns
the prompt assembly, the fallback chain across providers, and the
never-raises contract the pipelines depend on.

`GeneratedImage` and `ImageGenerationError` live here rather than there
because these functions return and raise them while `wp_image` imports this
module -- the other direction would be an import cycle. `wp_image` re-exports
both, so its existing importers (`lib.crew.draft`, `lib.crew.socialpost`,
`scripts.crewai_content_pipeline`) are untouched.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

import httpx

IMAGEN_PREDICT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:predict"
GEMINI_GENCONTENT_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
IMAGEN_FAST = "imagen-4.0-fast-generate-001"
IMAGEN_STANDARD = "imagen-4.0-generate-001"
NANO_PRO = "gemini-3-pro-image-preview"


class ReferencePhoto(NamedTuple):
    """One photo attached to a generation call.

    The MIME type travels WITH the bytes because a reference is routinely a
    PNG while the endpoint's default is JPEG, and a mislabelled part is
    decoded wrong before the model ever sees it.
    """

    bytes_: bytes
    mime: str = "image/png"


@dataclass
class GeneratedImage:
    """One generated (or placeholder) hero image."""

    url: str
    alt_text: str
    provider: str
    bytes_: bytes | None = None
    content_type: str = "image/png"


class ImageGenerationError(RuntimeError):
    """Raised by a single provider attempt; never escapes `generate_wp_image`."""


def call_imagen(
    prompt: str, *, model: str, provider: str, key: str, timeout: float = 60.0
) -> GeneratedImage:
    """Call the Gemini Imagen predict endpoint and return raw image bytes."""
    url = IMAGEN_PREDICT_ENDPOINT.format(model=model)
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {"sampleCount": 1, "aspectRatio": "16:9", "personGeneration": "dont_allow"},
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
        url=f"imagen://{model}", alt_text="", provider=provider, bytes_=raw, content_type=mime
    )


def call_nano_pro(
    prompt: str,
    *,
    key: str,
    references: Sequence[ReferencePhoto] = (),
    timeout: float = 180.0,
) -> GeneratedImage:
    """Call Gemini 3 Pro Image ("nano_pro") via `generateContent`.

    Same request shape as `recipe-publisher/generators/image.py::
    _generate_nano_pro` -- third fallback tier, see `wp_image`'s module
    docstring. Every reference is sent as its own `inline_data` part BEFORE
    the text part: `generateContent` treats them as visual context the text
    prompt then describes a new scene for (subject-consistency
    conditioning), not merely as attachments.

    **Order is meaningful and is the caller's to choose.** The prompt refers
    to the photos by position ("PHOTO 1", "PHOTO 2" --
    `lib.crew.reference_clauses.paired_reference_clause`), so parts are
    appended in the order given and never re-sorted here.
    """
    url = GEMINI_GENCONTENT_ENDPOINT.format(model=NANO_PRO)
    request_parts: list[dict[str, object]] = [
        {
            "inline_data": {
                "mime_type": photo.mime,
                "data": base64.b64encode(photo.bytes_).decode("ascii"),
            }
        }
        for photo in references
        if photo.bytes_
    ]
    request_parts.append({"text": prompt})
    payload = {
        "contents": [{"parts": request_parts}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "16:9"},
        },
    }
    try:
        r = httpx.post(url, params={"key": key}, json=payload, timeout=timeout)
    except httpx.HTTPError as e:
        raise ImageGenerationError(f"nano_pro request error: {e}") from e
    if r.status_code >= 400:
        raise ImageGenerationError(f"nano_pro HTTP {r.status_code}: {r.text[:300]}")

    data = r.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise ImageGenerationError(f"nano_pro: no candidates in {data!r}")
    parts = candidates[0].get("content", {}).get("parts", [])
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data") or {}
        if inline.get("data"):
            raw = base64.b64decode(inline["data"])
            mime = inline.get("mimeType", "image/jpeg")
            return GeneratedImage(
                url=f"nano_pro://{NANO_PRO}",
                alt_text="",
                provider="nano_pro",
                bytes_=raw,
                content_type=mime,
            )
    raise ImageGenerationError(f"nano_pro: no image bytes in parts {parts!r}")
