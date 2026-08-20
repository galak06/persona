"""The photo that anchors the brand's own mascot, whatever scene was asked for.

`lib.crew.reference_library.resolve_reference` answers ONE question: which
photo matches the tag the planner named. That is the right question for the
setting and the wrong one for the mascot, and a live compose showed why. The
plan for "Can Your Dog Eat Human-Grade Food Every Day?" named `home-exterior`;
the resolver correctly returned a cottage porch; the brief described an indoor
kitchen. The reference contributed nothing to the scene AND -- because a porch
photo carries `shows_mascot=False` -- the prompt clause it earned explicitly
told the model NOT to take the mascot's appearance from it. Nothing else was
attached, so the model invented an animal. The sibling post asked for
`persona-portrait`, got the person right, and invented a different animal
again; its caption came back tagged `#corgi`.

Telling the planner to name a mascot collection would be advice, not a
guarantee -- the same shape as the `general` escape hatch
`lib.crew.reference_vocabulary` had to delete. So the anchor is resolved in
CODE, independently of the tag: whenever the scene photo does not itself show
the mascot, the best photo that DOES is attached alongside it, and
`lib.crew.reference_clauses.paired_reference_clause` tells the model which
photo is doing which job.

Nothing here assumes what a mascot is. `shows_mascot` is the vision tagger's
verdict on the brand's own `site.mascot_kind`, whatever that is (see
`lib.crew.brand_identity`), and a brand whose library holds no such photo gets
`None` -- today's behaviour exactly, one reference and no anchor.
"""

from __future__ import annotations

from pathlib import Path

from lib.crew.reference_library import (
    Candidate,
    ReferenceImage,
    best_tier,
    existing_images_by_category,
    pick,
)
from lib.observability import get_logger

logger = get_logger(__name__)


def _mascot_candidates(brand_dir: Path) -> list[Candidate]:
    """Every photo in the library tagged `shows_mascot`, from any category.

    Category is deliberately ignored: the mascot's appearance is the same in a
    studio portrait and on a forest trail, so restricting the anchor to one tag
    would reintroduce the miss this module exists to prevent.
    """
    return [
        candidate
        for candidates in existing_images_by_category(brand_dir).values()
        for candidate in candidates
        if candidate[1].shows_mascot
    ]


def any_mascot_photo(brand_dir: Path, *, seed: str = "") -> ReferenceImage | None:
    """The best photo of the brand's mascot the library holds, or `None`.

    No scene and no category: this answers "show me the brand's own
    `mascot_kind`", which is a question the planner's tag cannot answer and an
    operator sometimes has to ask directly. It is what
    `lib.crew.socialpost.retry` falls back to when the plan named a collection
    the library cannot match -- an empty `reference_category` resolves to no
    photo, generation is skipped, and the post ships the WP hero. Re-running
    the same resolution would reproduce that outcome exactly, so a retry that
    cannot reach past the tag is a button that cannot succeed.

    Ranking matches `resolve_reference` -- uploads before WP-media harvests
    (`source_rank`), seeded pick inside the best tier only -- so the same
    `seed` picks the same photo, and a varying one spreads across the mascot
    photos the brand actually keeps.
    """
    candidates = _mascot_candidates(brand_dir)
    if not candidates:
        return None
    return pick(best_tier(candidates), seed)


def mascot_anchor(
    brand_dir: Path, scene: ReferenceImage | None, *, seed: str = ""
) -> ReferenceImage | None:
    """The photo that must ground the mascot's appearance, or `None`.

    `None` in three cases, all of them "already correct, attach nothing extra":

    * `scene` already shows the mascot -- one photo is doing both jobs, and a
      second would only give the model two versions of the same subject.
    * `scene` is `None` -- there is no generation to add a reference to (the
      caller does not generate at all without a scene photo, and an anchor may
      never become the scene by DEFAULT: the planner's tag decides the scene).
      An operator asking for a different outcome is not a default, which is why
      `any_mascot_photo` exists beside this and is called only from the retry
      path.
    * the library holds no `shows_mascot` photo -- nothing may substitute for
      one, exactly as `resolve_reference` refuses to substitute for a category.
    """
    if scene is None or scene.shows_mascot:
        return None
    anchor = any_mascot_photo(brand_dir, seed=seed)
    if anchor is None:
        logger.info("reference_library_no_mascot_photo", scene_category=scene.category)
    return anchor
