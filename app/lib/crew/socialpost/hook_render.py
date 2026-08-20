"""Scene photo -> generated hook image. The half both compose and retry share.

`lib.crew.socialpost.compose` resolves WHICH photo a post's hook image is
anchored on (the plan's `reference_category`, via the tagged library); this
module is everything that happens once one is chosen -- read the bytes, attach
the mascot anchor when the scene photo cannot carry the mascot itself, pick the
prompt clause that introduces both, and call the image model.

It lives on its own because there is now a SECOND caller with a different way
of choosing the photo. `lib.crew.socialpost.retry` regenerates the image of a
post already sitting in the review queue, and the whole point of that path is
that the planner's tag is not what decides the anchor -- the operator's pick
is, or failing that any photo of the mascot. Everything after the choice must
be identical to the composed path, or a retried image stops being the same kind
of artefact as a composed one; sharing the code is what makes that true rather
than hoped for.

Nothing here decides what to do when there is no photo. Both callers answer
that themselves, and they answer it differently: compose ships the WP hero
(`source='fallback'`), retry changes nothing at all.
"""

from __future__ import annotations

from pathlib import Path

from lib.crew.reference_clauses import paired_reference_clause, reference_clause
from lib.crew.reference_library import ReferenceImage
from lib.crew.reference_mascot import mascot_anchor
from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.wp_image import ReferencePhoto, generate_wp_image
from lib.observability import get_logger

logger = get_logger(__name__)


def read_reference_bytes(reference: ReferenceImage, *, role: str) -> bytes | None:
    """The photo's bytes, or `None` (logged) if it vanished since the manifest
    was read. Never fatal and never a licence to substitute another picture:
    the caller drops the whole generation, or drops the anchor.
    """
    try:
        return reference.path.read_bytes()
    except OSError as exc:
        logger.warning(
            "social_posts_reference_unmatched",
            requested_category=reference.category,
            reason="unreadable",
            role=role,
            path=str(reference.path),
            error=str(exc),
        )
        return None


def _anchor(
    scene: ReferenceImage, brand_dir: Path, *, seed: str
) -> tuple[ReferencePhoto, ReferenceImage] | None:
    """The extra photo that grounds the brand's mascot, or `None`.

    `None` whenever the scene photo already shows the mascot, the library
    holds no photo that does, or the one it holds cannot be read -- in every
    case generation still happens, with the scene photo alone.
    """
    anchor = mascot_anchor(brand_dir, scene, seed=seed)
    if anchor is None:
        return None
    data = read_reference_bytes(anchor, role="mascot_anchor")
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


def render_from_reference(
    plan: SocialPostPlan,
    scene: ReferenceImage,
    *,
    brand_dir: Path,
    seed: str = "",
    mascot_name: str = "",
    mascot_kind: str = "",
    persona_name: str = "",
) -> bytes | None:
    """Generated image bytes for `plan`, anchored on `scene`, or `None`.

    `None` covers every way of not getting an image -- the photo vanished, the
    model returned no bytes, the call raised -- because to both callers those
    are the same event: no new image, keep whatever the post already has.

    **A second photo goes with the scene whenever the first cannot carry the
    mascot.** A scene collection ("home exterior") is a porch, not the brand's
    animal, and the grounding clause a porch earns explicitly forbids taking
    the mascot from it -- so the mascot in the finished image was invented
    every time the planner named anything but a mascot collection. `_anchor`
    adds a `shows_mascot` photo alongside, and `paired_reference_clause` tells
    the model which is which. Nothing extra is attached when the scene photo
    already shows the mascot, or when the brand keeps no such photo.

    Passing a reference deliberately routes `generate_wp_image` past the
    Imagen tiers to `gemini-3-pro-image-preview` -- the only tier that accepts
    image input (see that function's docstring).
    """
    scene_bytes = read_reference_bytes(scene, role="scene")
    if scene_bytes is None:
        return None
    anchored = _anchor(scene, brand_dir, seed=seed)
    # A library photo of a product or a location must NOT be introduced as
    # "the brand's mascot" -- and one showing the person behind the brand
    # must name THEM, not the mascot. Both words are the brand's own; this
    # engine may never assume either (see `lib.crew.brand_identity`).
    clause = (
        reference_clause(scene, mascot_name, mascot_kind, persona_name)
        if anchored is None
        else paired_reference_clause(scene, anchored[1], mascot_name, mascot_kind, persona_name)
    )
    try:
        generated = generate_wp_image(
            plan.image_brief,
            alt_hint=plan.image_alt_text,
            mascot_name=mascot_name,
            reference_image_bytes=scene_bytes,
            reference_image_mime=scene.content_type,
            # Order is the contract `paired_reference_clause` describes:
            # PHOTO 1 is the scene, PHOTO 2 the mascot anchor.
            extra_reference_images=() if anchored is None else (anchored[0],),
            reference_clause=clause,
        )
    except Exception as exc:
        logger.warning("social_posts_image_generation_failed", error=str(exc))
        return None
    if generated.bytes_:
        return generated.bytes_
    logger.warning("social_posts_image_no_bytes", provider=generated.provider)
    return None
