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
together, labelled by position in the prompt -- see
`lib.crew.socialpost.hook_render`, which owns everything downstream of the
choice and is shared with `lib.crew.socialpost.retry`.

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

from lib.crew.reference_library import ReferenceImage, resolve_reference
from lib.crew.socialpost.hook_render import render_from_reference
from lib.crew.socialpost.models import SocialPostPlan
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


def _reference(plan: SocialPostPlan, brand_dir: Path, *, idea_id: str) -> ReferenceImage | None:
    """The library photo matching `plan.reference_category`, or `None` when
    there is no such photo -- which means DON'T GENERATE.

    The category is passed to the resolver verbatim -- "" and an unrecognised
    label are its business (it falls through to `general`, then to nothing),
    not this caller's.

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
    return reference


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

    That second case is a dead end for the operator as well as for the run:
    the post lands in the review queue carrying a stock image, and re-running
    this function against the same plan reproduces it exactly. Escaping it is
    `lib.crew.socialpost.retry`'s job, not this one's -- an unattended run may
    not go looking for some other photo when the plan's tag matched nothing
    (see `lib.crew.reference_library.resolve_reference` on why silent
    substitution is worse than a visible fallback).

    Everything after the photo is chosen lives in
    `lib.crew.socialpost.hook_render`, shared with that retry path."""
    reference = _reference(plan, brand_dir, idea_id=idea_id)
    if reference is None:
        return hero_bytes, "fallback"
    generated = render_from_reference(
        plan,
        reference,
        brand_dir=brand_dir,
        seed=idea_id,
        mascot_name=mascot_name,
        mascot_kind=mascot_kind,
        persona_name=persona_name,
    )
    return (generated, "gemini") if generated else (hero_bytes, "fallback")


def compose_image(
    plan: SocialPostPlan,
    *,
    idea_id: str,
    image_bytes: bytes,
    brand_dir: Path,
    ig_handle: str,
    filename_stem: str = "",
) -> str | None:
    """Overlay headline/subcopy + CTA ribbon + follow badge onto the image and
    write it under `$BRAND_DIR/state/social_posts_pending/`. Returns the
    BRAND_DIR-relative path, or None on failure.

    `filename_stem` defaults to `idea_id`, which is what composition wants: one
    post, one file, overwritten if the post is ever composed twice. A REGENERATED
    image must not take that name -- overwriting is a destructive act against the
    image the operator is currently reviewing, performed before the replacement is
    known to be good and before the DB has agreed to it. `lib.crew.socialpost.retry`
    therefore passes a distinct stem, and only unlinks the superseded file once
    the row points at the new one.

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

        relative = f"{PENDING_DIR}/{filename_stem or idea_id}.jpg"
        target = brand_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(composed)
        return relative
    except Exception as exc:
        logger.error("social_posts_compose_failed", idea_id=idea_id, error=str(exc))
        return None
