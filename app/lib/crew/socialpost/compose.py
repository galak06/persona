"""Hook-image production for the social-post pipeline.

The composition half of `scripts/crewai_social_posts_pipeline.py`, split out
for file-size discipline: everything from "we have a validated
`SocialPostPlan`" to "there is a finished JPEG under
`$BRAND_DIR/state/social_posts_pending/`".

One image serves both platforms (see `lib.crew.socialpost.models` on why the
captions don't). Generation is Gemini via `lib.crew.wp_image`, conditioned on
the WP post's OWN featured image as the visual reference -- the same
hero-as-reference choice the reels crew made for OpenArt, and for the same
live-confirmed reason: without a reference the model has no idea what the
brand's actual dog looks like and invents a generic one. The hero also
grounds the generated scene in this post's real subject matter, not just the
dog. On any generation failure that same hero is used directly as the image
(`source='fallback'`), overlays applied either way.
"""

from __future__ import annotations

import sys
from pathlib import Path

from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.wp_image import generate_wp_image
from lib.observability import get_logger

logger = get_logger(__name__)

_RECIPE_PUBLISHER_ROOT = Path(__file__).resolve().parents[3] / "recipe-publisher"
PENDING_DIR = "state/social_posts_pending"


def _sniff_mime(image_bytes: bytes) -> str:
    """JPEG vs PNG from magic bytes -- the WP hero arrives as raw bytes with
    no filename to go by."""
    return "image/jpeg" if image_bytes[:2] == b"\xff\xd8" else "image/png"


def generate_hook_image(
    plan: SocialPostPlan, *, mascot_name: str, hero_bytes: bytes
) -> tuple[bytes, str]:
    """The single shared image: Gemini-generated from the plan's brief with
    the WP post's own hero image as the visual reference, or that same hero
    used directly on any failure. Returns (bytes, source) with source in
    ('gemini', 'fallback').

    Passing a reference deliberately routes `generate_wp_image` past the
    Imagen tiers to `gemini-3-pro-image-preview` -- the only tier that
    accepts image input (see that function's docstring)."""
    try:
        generated = generate_wp_image(
            plan.image_brief,
            alt_hint=plan.image_alt_text,
            mascot_name=mascot_name,
            reference_image_bytes=hero_bytes,
            reference_image_mime=_sniff_mime(hero_bytes),
        )
        if generated.bytes_:
            return generated.bytes_, "gemini"
        logger.warning("social_posts_image_no_bytes", provider=generated.provider)
    except Exception as exc:
        logger.warning("social_posts_image_generation_failed", error=str(exc))
    return hero_bytes, "fallback"


def compose_image(
    plan: SocialPostPlan,
    *,
    idea_id: str,
    image_bytes: bytes,
    brand_dir: Path,
    ig_handle: str,
) -> str | None:
    """Overlay headline/subcopy + CTA ribbon + follow badge onto the image and
    write it under `$BRAND_DIR/state/social_posts_pending/`. Returns the
    BRAND_DIR-relative path, or None on failure.

    `text_overlay` lives in the recipe-publisher tree, which is not a package
    on this side's import path -- so extend `sys.path` the same way
    `recipe-publisher`'s own workers do, rather than subprocessing for what is
    a pure in-process image operation (the reels pipeline subprocesses because
    it needs ffmpeg's CLI; there's no such boundary here).
    """
    if str(_RECIPE_PUBLISHER_ROOT) not in sys.path:
        sys.path.insert(0, str(_RECIPE_PUBLISHER_ROOT))
    try:
        from generators.text_overlay import (
            OverlaySpec,
            apply_follow_badge,
            apply_overlay,
            apply_site_cta_ribbon,
        )

        composed = apply_overlay(
            image_bytes,
            OverlaySpec(headline=plan.overlay_headline, subcopy=plan.overlay_subcopy),
        )
        composed = apply_site_cta_ribbon(composed, plan.cta_ribbon_text)
        composed = apply_follow_badge(composed, handle=ig_handle)

        relative = f"{PENDING_DIR}/{idea_id}.jpg"
        target = brand_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(composed)
        return relative
    except Exception as exc:
        logger.error("social_posts_compose_failed", idea_id=idea_id, error=str(exc))
        return None
