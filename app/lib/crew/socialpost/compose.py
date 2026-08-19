"""Hook-image production for the social-post pipeline.

The composition half of `scripts/crewai_social_posts_pipeline.py`, split out
for file-size discipline: everything from "we have a validated
`SocialPostPlan`" to "there is a finished JPEG under
`$BRAND_DIR/state/social_posts_pending/`".

One image serves both platforms (see `lib.crew.socialpost.models` on why the
captions don't). Generation is Gemini via `lib.crew.wp_image`, conditioned on
a real brand photo picked from the tagged library
(`lib.crew.reference_library`) by the scene the plan says the image shows
(`SocialPostPlan.reference_category`) -- and introduced to the model as what
it actually is -- mascot, persona, both or neither (`lib.crew.reference_clauses`).

**Plus, when that photo shows no mascot, a photo that does**
(`lib.crew.reference_mascot`). One reference cannot both hold the setting and
hold the brand's animal, and the plan only ever names one collection: asked
for `home-exterior`, the model got a porch and invented a dog to put in the
kitchen the brief described. The scene photo and the mascot photo now travel
together, labelled by position in the prompt.

That reference used to be the WP post's OWN featured image, on the theory
that it grounds the scene in this post's subject matter. It did -- but a hero
is routinely a Pexels stock photo, so the "brand's mascot" in every generated
hook image was a stranger's. Same bug the reels pipeline had, third home.

**Only an uploaded photo may anchor a generated image.** The hero is now
purely the FALLBACK OUTPUT (`source='fallback'`): it is what the post ships
when generation fails -- and when the library has no photo for this plan's
scene, in which case Gemini is never called at all. Overlays are applied
either way, so the post looks finished regardless.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image

from lib.crew.reference_clauses import paired_reference_clause, reference_clause
from lib.crew.reference_library import ReferenceImage, resolve_reference
from lib.crew.reference_mascot import mascot_anchor
from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.wp_image import ReferencePhoto, generate_wp_image
from lib.observability import get_logger

logger = get_logger(__name__)

_RECIPE_PUBLISHER_ROOT = Path(__file__).resolve().parents[3] / "recipe-publisher"
PENDING_DIR = "state/social_posts_pending"


def center_crop_square(image_bytes: bytes) -> bytes:
    """Crop to 1:1, centered -- the same trick `worker_wp_ideas_reel.py`'s
    `_center_crop_to_9_16` plays for Reels, with a square target.

    Two things go wrong without this, both seen on the first live post:

    * Gemini returns 16:9 (1376x768 in practice). Instagram letterboxes a
      wide image in the feed, so the post shows black bars instead of photo.
    * `text_overlay.apply_overlay`'s defaults (`headline_y_pct=0.72`,
      `band_top_pct=0.58`) are calibrated for 1:1. On a 16:9 frame those
      fractions land much lower in absolute pixels, so the subcopy collides
      with `apply_site_cta_ribbon`'s bottom band and gets painted over --
      "Skipping this could hurt your dog" was sliced in half.

    Cropping first fixes both, and lets every downstream overlay keep the
    geometry it was designed against.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    side = min(w, h)
    if w == h:
        return image_bytes
    left = (w - side) // 2
    top = (h - side) // 2
    cropped = img.crop((left, top, left + side, top + side))
    out = io.BytesIO()
    cropped.save(out, "JPEG", quality=95)
    return out.getvalue()


def _read(reference: ReferenceImage, *, requested_category: str, role: str) -> bytes | None:
    """The photo's bytes, or `None` (logged) if it vanished since the manifest
    was read. Never fatal and never a licence to substitute another picture:
    the caller drops the scene photo's whole generation, or drops the anchor.
    """
    try:
        return reference.path.read_bytes()
    except OSError as exc:
        logger.warning(
            "social_posts_reference_unmatched",
            requested_category=requested_category,
            reason="unreadable",
            role=role,
            path=str(reference.path),
            error=str(exc),
        )
        return None


def _reference(
    plan: SocialPostPlan, brand_dir: Path, *, idea_id: str
) -> tuple[bytes, str, ReferenceImage] | None:
    """(bytes, mime, entry) to condition generation on -- the library photo
    matching `plan.reference_category` -- or `None` when there is no such
    photo, which means DON'T GENERATE. The entry travels with the bytes so
    the caller can pick the prompt clause from `shows_mascot`.

    The category is passed to the resolver verbatim -- "" and an unrecognised
    label are its business (it falls through to `general`, then to any other
    tag), not this caller's.

    `idea_id` is the resolver's `seed`. Without one every post naming a
    category got that category's first photo by id, forever -- a brand with
    four `forest-trail` photos published the same one every time. Seeding on
    the idea spreads them while keeping a re-run of one post reproducible,
    which is what `scripts.reels_images` does per beat.
    """
    reference = resolve_reference(brand_dir, plan.reference_category, seed=idea_id)
    if reference is None:
        logger.info(
            "social_posts_reference_unmatched",
            requested_category=plan.reference_category,
            reason="no_library_match",
        )
        return None
    data = _read(reference, requested_category=plan.reference_category, role="scene")
    if data is None:
        return None
    logger.info(
        "social_posts_reference_selected",
        requested_category=plan.reference_category,
        category=reference.category,
        image_id=reference.id,
        shows_mascot=reference.shows_mascot,
        # Both flags, because `reference_clause` branches on both: with only
        # `shows_mascot` in the log, an identity-anchored image and a merely
        # grounded one are indistinguishable without opening `library.json`.
        shows_persona=reference.shows_persona,
    )
    return data, reference.content_type, reference


def _anchor(
    scene: ReferenceImage, brand_dir: Path, *, idea_id: str
) -> tuple[ReferencePhoto, ReferenceImage] | None:
    """The extra photo that grounds the brand's mascot, or `None`.

    `None` whenever the scene photo already shows the mascot, the library
    holds no photo that does, or the one it holds cannot be read -- in every
    case generation still happens, with the scene photo alone, exactly as
    before this existed.
    """
    anchor = mascot_anchor(brand_dir, scene, seed=idea_id)
    if anchor is None:
        return None
    data = _read(anchor, requested_category=anchor.category, role="mascot_anchor")
    if data is None:
        return None
    logger.info(
        "social_posts_mascot_anchor_attached",
        scene_image_id=scene.id,
        image_id=anchor.id,
        category=anchor.category,
        shows_persona=anchor.shows_persona,
    )
    return ReferencePhoto(data, anchor.content_type), anchor


def generate_hook_image(
    plan: SocialPostPlan,
    *,
    mascot_name: str,
    hero_bytes: bytes,
    brand_dir: Path,
    idea_id: str = "",
    mascot_kind: str = "",
    persona_name: str = "",
) -> tuple[bytes, str]:
    """The single shared image: Gemini-generated from the plan's brief,
    conditioned on a photo from the brand's library (see `_reference`), with
    the WP hero used directly as the image otherwise. Returns (bytes, source)
    with source in ('gemini', 'fallback').

    `fallback` covers both ways of not getting a generated image: generation
    failed, or the library had no photo to anchor it on -- in which case
    nothing is generated, because only an uploaded photo may serve as the
    reference and the hero (routinely stock) is not one.

    **A second photo goes with it whenever the first cannot carry the
    mascot.** The plan names ONE collection, and a scene collection ("home
    exterior") is a porch, not the brand's animal -- so the mascot in the
    finished image was invented every time the planner named anything but a
    mascot collection. `_anchor` adds a `shows_mascot` photo alongside the
    scene photo, and `paired_reference_clause` tells the model which is which.
    Nothing extra is attached when the scene photo already shows the mascot,
    or when the brand keeps no such photo.

    Passing a reference deliberately routes `generate_wp_image` past the
    Imagen tiers to `gemini-3-pro-image-preview` -- the only tier that
    accepts image input (see that function's docstring)."""
    resolved = _reference(plan, brand_dir, idea_id=idea_id)
    if resolved is None:
        return hero_bytes, "fallback"
    reference_bytes, reference_mime, reference = resolved
    anchored = _anchor(reference, brand_dir, idea_id=idea_id)
    # A library photo of a product or a location must NOT be introduced as
    # "the brand's mascot" -- and one showing the person behind the brand
    # must name THEM, not the mascot. Both words are the brand's own; this
    # engine may never assume either (see `lib.crew.brand_identity`).
    clause = (
        reference_clause(reference, mascot_name, mascot_kind, persona_name)
        if anchored is None
        else paired_reference_clause(reference, anchored[1], mascot_name, mascot_kind, persona_name)
    )
    try:
        generated = generate_wp_image(
            plan.image_brief,
            alt_hint=plan.image_alt_text,
            mascot_name=mascot_name,
            reference_image_bytes=reference_bytes,
            reference_image_mime=reference_mime,
            # Order is the contract `paired_reference_clause` describes:
            # PHOTO 1 is the scene, PHOTO 2 the mascot anchor.
            extra_reference_images=() if anchored is None else (anchored[0],),
            reference_clause=clause,
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

        # Square FIRST -- every overlay below is calibrated against 1:1.
        square = center_crop_square(image_bytes)
        composed = apply_overlay(
            square,
            OverlaySpec(headline=plan.overlay_headline, subcopy=plan.overlay_subcopy),
            # Lift the text clear of the CTA ribbon's band at the bottom.
            # apply_overlay's 0.72 default assumes nothing is painted under it.
            headline_y_pct=0.62,
            band_top_pct=0.46,
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
