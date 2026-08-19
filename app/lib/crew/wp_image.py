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
API (confirmed working live, real ~1.1MB JPEG returned). Adapted as
`lib.crew.wp_image_providers.call_nano_pro`, same shape as that module's
`_generate_nano_pro`, kept in the same fallback chain (after both Imagen
tiers) so a working provider remains once Imagen access is restored.

The provider calls themselves live in `lib.crew.wp_image_providers` (this
file was at its 300-line ceiling); what stays here is the prompt, the
fallback chain and the never-raises contract: `generate_wp_image` returns a
placeholder `GeneratedImage` (`url="placeholder"`, `bytes_=b""`) if every
provider fails, so `lib.crew.draft.create_wp_draft` can detect and skip the
image step gracefully (best-effort, matches `_maybe_attach_affiliate_block`'s
"never blocks publish" convention in
`recipe-publisher/publishers/wordpress.py`).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from lib.crew.reference_clauses import identity_clause
from lib.crew.reference_legacy import (
    resolve_reference_image_path as _resolve_reference_image_path,
)
from lib.crew.wp_image_providers import (
    IMAGEN_FAST,
    IMAGEN_STANDARD,
    GeneratedImage,
    ImageGenerationError,
    ReferencePhoto,
    call_imagen,
    call_nano_pro,
)
from lib.observability import get_logger

logger = get_logger(__name__)

#: Re-exported for the modules that have always imported them from here.
__all__ = [
    "GeneratedImage",
    "ImageGenerationError",
    "ReferencePhoto",
    "build_image_brief",
    "generate_wp_image",
    "resolve_reference_image_path",
]

_NEGATIVES = " No text, labels, watermarks, logos, or packaging."


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
    """Delegating alias for `lib.crew.reference_legacy.resolve_reference_image_path`.

    The legacy-asset probe moved to `reference_legacy`, apart from the tagged
    library that replaced it; this alias keeps every existing importer --
    `scripts/crewai_content_pipeline.py`, `scripts/reels_images.py`, and their
    tests, which monkeypatch it by module attribute -- working unchanged.
    """
    return _resolve_reference_image_path(brand_dir)


def _style_suffix(mascot_name: str, *, has_reference: bool, reference_clause: str = "") -> str:
    if has_reference:
        # The caller's verdict on WHAT the attached photo shows (built by
        # `lib.crew.reference_clauses.reference_clause`). Callers that don't
        # express one keep the historical assumption: it is the mascot.
        mascot_clause = reference_clause or identity_clause(mascot_name)
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
    extra_reference_images: Sequence[ReferencePhoto] = (),
    reference_clause: str = "",
) -> GeneratedImage:
    """Generate a topic-appropriate hero image -- never food-styled.

    `reference_clause` (optional): what to tell the model the attached
    photo(s) ARE -- build it with `lib.crew.reference_clauses.reference_clause`
    for one photo or `paired_reference_clause` for two. Left empty, the
    reference is assumed to show the mascot (historical).

    `reference_image_bytes` (optional): a real photo of the brand's persona
    and/or mascot to condition generation on, so the hero image actually
    depicts them instead of a generic AI-invented person/dog. Only
    `gemini-3-pro-image-preview` ("nano_pro") accepts image inputs this way
    -- Imagen's `predict` endpoint has no reference-image parameter, so
    supplying a reference SKIPS the Imagen tiers entirely (attempting them
    would silently produce a non-matching generic image and defeat the
    purpose, since they'd succeed before ever reaching nano_pro).

    `extra_reference_images` (optional): FURTHER photos, sent after that one
    and in the order given, because one photo cannot both hold the scene and
    hold the brand's mascot. A scene collection ("home exterior") anchored a
    kitchen brief while nothing at all anchored the mascot, so the model
    invented one; `lib.crew.socialpost.compose` now sends the scene photo
    plus a `shows_mascot` photo and a clause that says which is which.
    Ignored without a primary reference: the positional clause describes
    "PHOTO 1" first, and there is no photo 1 without one.

    Without any reference image, tries Imagen 4 Fast, then Imagen 4 Standard,
    then nano_pro (see module docstring on why this third tier was added).
    Returns a placeholder `GeneratedImage` (`bytes_=b""`) on total failure
    (missing `GEMINI_API_KEY`, or every provider erroring) so the caller can
    skip the upload step gracefully rather than fail draft creation.
    """
    key = os.environ.get("GEMINI_API_KEY", "")
    alt_text = alt_hint or brief[:80]
    references: list[ReferencePhoto] = (
        [ReferencePhoto(reference_image_bytes, reference_image_mime), *extra_reference_images]
        if reference_image_bytes
        else []
    )
    full_prompt = (
        f"{brief}. "
        f"{_style_suffix(mascot_name, has_reference=bool(references), reference_clause=reference_clause)}"
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

    providers = ("nano_pro",) if references else ("imagen_fast", "imagen_standard", "nano_pro")
    for provider in providers:
        try:
            if provider == "imagen_fast":
                img = call_imagen(full_prompt, model=IMAGEN_FAST, provider=provider, key=key)
            elif provider == "imagen_standard":
                img = call_imagen(full_prompt, model=IMAGEN_STANDARD, provider=provider, key=key)
            else:
                img = call_nano_pro(full_prompt, key=key, references=references)
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
