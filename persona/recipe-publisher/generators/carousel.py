"""IG carousel generation: seed -> 4 generated images with text overlays.

Loads `seeds/carousels/{seed_id}.json`, calls the image provider for each
slide's cinematic prompt, renders the deterministic PIL text overlay on top,
and returns a list of GeneratedImage objects ready for the IG publisher to
upload to WP + stitch into a CAROUSEL_ALBUM.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path

from .image import (
    GeneratedImage,
    ImageGenerationError,
    _generate_imagen,
    _generate_nano_pro,
    _IMAGEN_STANDARD,
)
from .text_overlay import (
    OverlaySpec,
    apply_follow_badge,
    apply_image_badge,
    apply_overlay,
    apply_site_cta_ribbon,
)

logger = logging.getLogger(__name__)

_CAROUSELS_DIR = Path(__file__).parent.parent / "seeds" / "carousels"


@dataclass
class CarouselSlideConfig:
    key: str
    prompt: str
    headline: str
    subcopy: str


class NoCarouselConfigError(LookupError):
    """Raised when a seed has no carousel config — IG carousel is not available."""


def load_carousel_config(seed_id: str) -> tuple[list[CarouselSlideConfig], str, str]:
    """Return (slides, aspect_ratio, model) for the seed or raise NoCarouselConfigError."""
    path = _CAROUSELS_DIR / f"{seed_id}.json"
    if not path.exists():
        raise NoCarouselConfigError(
            f"No carousel config for seed {seed_id!r} at {path}. "
            f"Create it before publishing to IG."
        )
    raw = json.loads(path.read_text())
    slides = [
        CarouselSlideConfig(
            key=s["key"],
            prompt=s["prompt"],
            headline=s["overlay"]["headline"],
            subcopy=s["overlay"]["subcopy"],
        )
        for s in raw["slides"]
    ]
    return slides, raw.get("aspect_ratio", "1:1"), raw.get("model", "nano_pro")


def _build_base_slides(
    seed_id: str,
    *,
    recipe_title: str,
    site_cta: str,
) -> list[tuple[CarouselSlideConfig, GeneratedImage]]:
    """Expensive step: generate + text-overlay every slide (plus the final-slide
    CTA ribbon). The hero is left UN-branded so callers can stamp either the
    @handle follow pill (reel) or the Nalla-approved PNG badge (post) without
    re-running image generation.
    """
    slides, aspect_ratio, model = load_carousel_config(seed_id)
    # For 9:16 Reel-native slides, push text up to clear IG's bottom UI overlay
    # zone (caption + action buttons occupy the bottom ~20% on mobile).
    if aspect_ratio == "9:16":
        headline_y_pct, band_top_pct = 0.60, 0.46
    else:
        headline_y_pct, band_top_pct = 0.72, 0.58
    out: list[tuple[CarouselSlideConfig, GeneratedImage]] = []
    for slide in slides:
        logger.info("generating carousel slide=%s model=%s", slide.key, model)
        img = _generate_slide(slide, model=model, aspect_ratio=aspect_ratio)
        overlaid = apply_overlay(
            img.bytes_ or b"",
            OverlaySpec(headline=slide.headline, subcopy=slide.subcopy),
            headline_y_pct=headline_y_pct,
            band_top_pct=band_top_pct,
        )
        if slide.key == "final":
            overlaid = apply_site_cta_ribbon(overlaid, cta_text=site_cta)
        img.bytes_ = overlaid
        img.content_type = "image/jpeg"
        img.alt_text = _alt_for(recipe_title, slide)
        out.append((slide, img))
    return out


def generate_carousel_slides(
    seed_id: str,
    *,
    recipe_title: str,
    ig_handle: str = "@persona",
    site_cta: str = "FULL RECIPE  \u2192  DOGFOODANDFUN.COM",
) -> list[GeneratedImage]:
    """Generate + overlay all slides for a seed. One GeneratedImage per slide.

    Slide 1 (hero) gets the @handle follow pill; slide 4 (final) gets a bottom
    CTA ribbon. This is the reel/legacy branding \u2014 the carousel POST instead
    uses ``generate_post_and_reel_slides`` to swap the hero pill for the seal.
    """
    out: list[GeneratedImage] = []
    for slide, img in _build_base_slides(
        seed_id, recipe_title=recipe_title, site_cta=site_cta
    ):
        if slide.key == "hero":
            img.bytes_ = apply_follow_badge(img.bytes_ or b"", handle=ig_handle)
        out.append(img)
    return out


def generate_post_and_reel_slides(
    seed_id: str,
    *,
    recipe_title: str,
    badge_path: str | None = None,
    ig_handle: str = "@persona",
    site_cta: str = "FULL RECIPE  \u2192  DOGFOODANDFUN.COM",
) -> tuple[list[GeneratedImage], list[bytes]]:
    """Generate slides ONCE and brand the hero two ways for its two consumers.

    Returns ``(post_slides, reel_bytes)``:

    - ``post_slides`` \u2014 the IG/FB carousel POST. Hero carries the Nalla-approved
      PNG badge in the top-right (replacing the @handle pill) when ``badge_path``
      is given; falls back to the pill when it is None.
    - ``reel_bytes`` \u2014 raw slide bytes for the reel video. Hero keeps the
      @handle follow pill; the reel branding is intentionally unchanged.

    Non-hero slides are shared verbatim between both outputs.
    """
    post: list[GeneratedImage] = []
    reel_bytes: list[bytes] = []
    for slide, img in _build_base_slides(
        seed_id, recipe_title=recipe_title, site_cta=site_cta
    ):
        base = img.bytes_ or b""
        if slide.key == "hero":
            reel_b = apply_follow_badge(base, handle=ig_handle)
            post_b = (
                apply_image_badge(base, badge_path, corner="top_right", width_pct=0.14)
                if badge_path
                else reel_b
            )
        else:
            post_b = reel_b = base
        post.append(replace(img, bytes_=post_b))
        reel_bytes.append(reel_b)
    return post, reel_bytes


def _generate_slide(
    slide: CarouselSlideConfig,
    *,
    model: str,
    aspect_ratio: str,
) -> GeneratedImage:
    if model == "nano_pro":
        return _generate_nano_pro(slide.prompt, aspect_ratio=aspect_ratio)
    if model in {"imagen_standard", "imagen_fast"}:
        # Imagen via predict — aspect ratio is a top-level parameter on that endpoint.
        imagen_model = _IMAGEN_STANDARD if model == "imagen_standard" else None
        if imagen_model is None:
            raise ImageGenerationError(
                f"carousel model {model!r} not wired for Imagen chain"
            )
        return _generate_imagen(slide.prompt, model=imagen_model, provider=model)
    raise ImageGenerationError(f"unknown carousel model: {model!r}")


def _alt_for(recipe_title: str, slide: CarouselSlideConfig) -> str:
    # Short, descriptive alt text based on the slide key + title.
    role = {
        "hero": "finished dish",
        "ingredients": "ingredient flat-lay",
        "process": "cooking process",
        "final": "finished dish cooling on rack",
    }.get(slide.key, slide.key.replace("_", " "))
    return f"{recipe_title} — {role}"
