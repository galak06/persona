"""Hero-image generation for CrewAI-drafted WordPress posts.

Adapted from `recipe-publisher/publishers/_wordpress_ideas_helpers.py::
generate_idea_image` -- same Imagen 4 Fast -> Imagen 4 Standard fallback
chain via `GEMINI_API_KEY`, same "no food styling" editorial guardrail
(this pipeline drafts general lifestyle/gear posts, not recipes --
`recipe-publisher/generators/image.py::generate_image()` hardcodes a
dog-treat/food-photography suffix onto every prompt that would be wrong
here, e.g. "homemade dog treats in a bowl" styling on a GPS-tracker post).

Kept self-contained rather than importing across the `recipe-publisher/` /
`lib/` package boundary -- those are different top-level trees with no
shared `sys.path` today (this pipeline is a deliberately standalone flow,
see `/Users/gilcohen/.claude/plans/immutable-frolicking-mitten.md`,
decision 1). Duplicating this one small Imagen-calling helper matches this
repo's existing precedent of several independent local `slugify()`
implementations (`lib.groups_db.models`, `lib.engagements_db.models`,
`recipe-publisher/publishers/wordpress_ideas.py`, ...) rather than adding a
cross-tree import dependency for a single function.

Third fallback tier, added during this build's real-validation run (not in
the reference implementation): live-tested `imagen-4.0-fast-generate-001`
and `imagen-4.0-generate-001` both now return HTTP 404 "no longer available
to new users" for this project's `GEMINI_API_KEY` -- a real, external
provider deprecation, not a bug in this code. `recipe-publisher/generators/
image.py` already carries the accepted fix for this exact situation: a
`gemini-3-pro-image-preview` ("nano_pro") tier via the `generateContent`
API (confirmed working live, real ~1.1MB JPEG returned). Adapted here as
`_call_nano_pro`, same shape as that module's `_generate_nano_pro`, kept in
the same fallback chain (after both Imagen tiers) so a working provider
remains once Imagen access is restored.

Never raises out of `generate_wp_image` -- returns a placeholder
`GeneratedImage` (`url="placeholder"`, `bytes_=b""`) if every provider
fails, so `lib.crew.draft.create_wp_draft` can detect and skip the image
step gracefully (best-effort, matches `_maybe_attach_affiliate_block`'s
"never blocks publish" convention in
`recipe-publisher/publishers/wordpress.py`).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from lib.crew.mascot_library import identity_clause
from lib.crew.mascot_library import resolve_reference_image_path as _resolve_reference_image_path
from lib.observability import get_logger

logger = get_logger(__name__)

_IMAGEN_PREDICT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:predict"
_GEMINI_GENCONTENT_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
_IMAGEN_FAST = "imagen-4.0-fast-generate-001"
_IMAGEN_STANDARD = "imagen-4.0-generate-001"
_NANO_PRO = "gemini-3-pro-image-preview"

_NEGATIVES = " No text, labels, watermarks, logos, or packaging."


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


def build_image_brief(title: str, mascot_angle: str) -> str:
    """Deterministic, no-LLM-call scene brief from data the strategist stage
    already produced.

    `ContentBrief.mascot_angle` ("how the brand's mascot/voice fits THIS
    specific topic -- grounded in the idea's own reasoning", see
    `lib.crew.writer.models.ContentBrief`) already describes a concrete,
    topic-grounded moment -- combined with the post title that's enough
    material for Imagen without an extra Gemini call to paraphrase it (the
    reference implementation's `_image_brief_for_idea` in
    `recipe-publisher/publishers/wordpress_ideas.py` spends one to build
    this from much thinner idea-row data; the CrewAI writer pipeline's own
    structured output is already richer).
    """
    parts = [p.strip() for p in (title, mascot_angle) if p and p.strip()]
    return ". ".join(parts)


def resolve_reference_image_path(brand_dir: Path) -> Path | None:
    """Delegating alias for `lib.crew.mascot_library.resolve_reference_image_path`.

    The legacy-asset probe moved to `mascot_library` (which also owns the new
    tagged reference library); this alias keeps every existing importer --
    `scripts/crewai_content_pipeline.py`, `scripts/reels_images.py`, and their
    tests, which monkeypatch it by module attribute -- working unchanged.
    """
    return _resolve_reference_image_path(brand_dir)


def _style_suffix(mascot_name: str, *, has_reference: bool) -> str:
    if has_reference:
        mascot_clause = identity_clause(mascot_name)
    else:
        mascot_clause = (
            f"If a dog appears in the scene, it is {mascot_name}. " if mascot_name else ""
        )
    return (
        f"{mascot_clause}Photorealistic photography, natural lighting, authentic home or "
        "outdoor setting. IMPORTANT: absolutely no dog food, no dog treats, no bowls, no "
        "cutting boards, no parchment paper, no kitchen counters with food. Focus on the "
        "scene described in the brief."
    )


def generate_wp_image(
    brief: str,
    *,
    alt_hint: str = "",
    mascot_name: str = "",
    reference_image_bytes: bytes | None = None,
    reference_image_mime: str = "image/png",
) -> GeneratedImage:
    """Generate a topic-appropriate hero image -- never food-styled.

    `reference_image_bytes` (optional): a real photo of the brand's persona
    and/or mascot to condition generation on, so the hero image actually
    depicts them instead of a generic AI-invented person/dog. Only
    `gemini-3-pro-image-preview` ("nano_pro") accepts image inputs this way
    -- Imagen's `predict` endpoint has no reference-image parameter, so
    supplying a reference SKIPS the Imagen tiers entirely (attempting them
    would silently produce a non-matching generic image and defeat the
    purpose, since they'd succeed before ever reaching nano_pro).

    Without a reference image, tries Imagen 4 Fast, then Imagen 4 Standard,
    then nano_pro (see module docstring on why this third tier was added).
    Returns a placeholder `GeneratedImage` (`bytes_=b""`) on total failure
    (missing `GEMINI_API_KEY`, or every provider erroring) so the caller can
    skip the upload step gracefully rather than fail draft creation.
    """
    key = os.environ.get("GEMINI_API_KEY", "")
    alt_text = alt_hint or brief[:80]
    full_prompt = (
        f"{brief}. {_style_suffix(mascot_name, has_reference=bool(reference_image_bytes))}"
        f"{_NEGATIVES}"
    )

    if not key:
        logger.warning("crew_draft_image_all_providers_failed")
        return GeneratedImage(
            url="placeholder",
            alt_text=alt_text,
            provider="none",
            bytes_=b"",
            content_type="image/png",
        )

    providers = (
        ("nano_pro",) if reference_image_bytes else ("imagen_fast", "imagen_standard", "nano_pro")
    )
    for provider in providers:
        try:
            if provider == "imagen_fast":
                img = _call_imagen(full_prompt, model=_IMAGEN_FAST, provider=provider, key=key)
            elif provider == "imagen_standard":
                img = _call_imagen(full_prompt, model=_IMAGEN_STANDARD, provider=provider, key=key)
            else:
                img = _call_nano_pro(
                    full_prompt,
                    key=key,
                    reference_image_bytes=reference_image_bytes,
                    reference_image_mime=reference_image_mime,
                )
            img.alt_text = alt_text
            logger.info(
                "crew_draft_image_generated", provider=provider, bytes_len=len(img.bytes_ or b"")
            )
            return img
        except ImageGenerationError as exc:
            logger.warning("crew_draft_image_provider_failed", provider=provider, error=str(exc))

    logger.warning("crew_draft_image_all_providers_failed")
    return GeneratedImage(
        url="placeholder", alt_text=alt_text, provider="none", bytes_=b"", content_type="image/png"
    )


def _call_imagen(
    prompt: str, *, model: str, provider: str, key: str, timeout: float = 60.0
) -> GeneratedImage:
    """Call the Gemini Imagen predict endpoint and return raw image bytes."""
    url = _IMAGEN_PREDICT_ENDPOINT.format(model=model)
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


def _call_nano_pro(
    prompt: str,
    *,
    key: str,
    reference_image_bytes: bytes | None = None,
    reference_image_mime: str = "image/png",
    timeout: float = 180.0,
) -> GeneratedImage:
    """Call Gemini 3 Pro Image ("nano_pro") via `generateContent`.

    Same request shape as `recipe-publisher/generators/image.py::
    _generate_nano_pro` -- third fallback tier, see module docstring. When
    `reference_image_bytes` is given, it's sent as an `inline_data` part
    BEFORE the text part -- `generateContent` treats it as visual context
    the text prompt then describes a new scene for (subject-consistency
    conditioning), not merely an attachment.
    """
    url = _GEMINI_GENCONTENT_ENDPOINT.format(model=_NANO_PRO)
    request_parts: list[dict[str, object]] = []
    if reference_image_bytes:
        request_parts.append(
            {
                "inline_data": {
                    "mime_type": reference_image_mime,
                    "data": base64.b64encode(reference_image_bytes).decode("ascii"),
                }
            }
        )
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
                url=f"nano_pro://{_NANO_PRO}",
                alt_text="",
                provider="nano_pro",
                bytes_=raw,
                content_type=mime,
            )
    raise ImageGenerationError(f"nano_pro: no image bytes in parts {parts!r}")
