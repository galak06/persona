"""Tagged reference-image library -- READ side.

A brand may keep any number of real photos to ground its generated imagery,
tagged by what they show (`forest-trail`, `studio-mascot`, `products`, ...,
plus a catch-all `general`) -- NOT all mascot portraits: a reference may just
as well be a product, a place, or a style plate. Generators pick the one whose
tag matches the beat they render, not the one legacy photo below. Tags are
meant to be SPECIFIC -- see `lib.crew.reference_vision_prompt` on why a library
that filed everything under `general` made `resolve_reference`'s pick arbitrary.

Layout under `$BRAND_DIR`::

    data/assets/
      persona_mascot_reference.png   # LEGACY -- never touched, never copied
      reference_images/
        library.json                 # manifest (see `read_manifest`)
        general/<sha256[:16]><ext>
        forest-trail/<sha256[:16]><ext>

Filenames are content-addressed, so the same bytes in the same category are
one file; the uploader's filename never reaches the filesystem (it survives
as the manifest entry's `label`).

Imported by the worker on every generated beat, so deliberately
dependency-light: **no Pillow, no FastAPI**. Byte validation lives in
`lib.crew.reference_validate` (the only PIL importer) and writes in
`lib.crew.reference_library_store`; the import direction is one-way.

**The library is the ONLY source of references.** Nothing uploaded elsewhere
may anchor a generated image -- not the WP post's hero (routinely a Pexels
stock photo), not the legacy `persona_mascot_reference.*` on disk, which is
import-only (see `lib.crew.reference_legacy`).

Every read is tolerant: a missing or malformed manifest reads as an empty
library, an entry whose file vanished is skipped with a warning, a stray file
with no entry is ignored. A brand with no library gets `None` from
`resolve_reference` -- "do not generate this image", not an error, and not a
licence to substitute some other picture.
"""

from __future__ import annotations

from pathlib import Path

from lib.crew.reference_manifest import (
    CONTENT_TYPE_BY_SUFFIX,
    GENERAL_CATEGORY,
    LIBRARY_DIRNAME,
    MANIFEST_FILENAME,
    Candidate,
    Manifest,
    ReferenceImage,
    assets_dir,
    best_tier,
    empty_manifest,
    existing_images_by_category,
    library_root,
    manifest_path,
    pick,
    read_manifest,
    slugify,
    source_rank,
)
from lib.observability import get_logger

logger = get_logger(__name__)

#: Re-exported so the modules that imported these from here keep working --
#: the split moved storage into `reference_manifest`, not out of the package.
__all__ = [
    "CONTENT_TYPE_BY_SUFFIX",
    "GENERAL_CATEGORY",
    "LIBRARY_DIRNAME",
    "MANIFEST_FILENAME",
    "Candidate",
    "Manifest",
    "ReferenceImage",
    "assets_dir",
    "best_tier",
    "empty_manifest",
    "existing_images_by_category",
    "library_root",
    "list_category_labels",
    "manifest_path",
    "pick",
    "read_manifest",
    "resolve_reference",
    "slugify",
    "source_rank",
]


def list_category_labels(brand_dir: Path, *, with_photos: bool = False) -> list[str]:
    """Human labels of every declared category, for prompt injection.

    Declared order first (that is the order an operator created them in),
    then any category that only exists on an image entry -- defensive, so a
    hand-edited manifest still advertises its tags.

    `with_photos=True` keeps only categories that actually hold a photo -- what a
    content PLANNER must be offered, since a declared-but-empty tag reads as a real
    choice yet resolves to no image at all (a live reel lost two of five beats to a
    `general` every photo had been re-tagged out of). The TAGGER wants them all: it
    is building the vocabulary, and an empty tag is still one of its words.
    """
    manifest = read_manifest(brand_dir)
    labels: dict[str, str] = {}
    for category in manifest["categories"]:
        slug = slugify(str(category.get("slug", "")))
        if slug and slug not in labels:
            labels[slug] = str(category.get("label") or slug)
    for image in manifest["images"]:
        slug = slugify(str(image.get("category", "")))
        if slug and slug not in labels:
            labels[slug] = slug.replace("-", " ")
    if not with_photos:
        return list(labels.values())
    stocked = existing_images_by_category(brand_dir)
    return [label for slug, label in labels.items() if slug in stocked]


def resolve_reference(
    brand_dir: Path, category: str | None, *, seed: str = "", prefer_mascot: bool = False
) -> ReferenceImage | None:
    """Best UPLOADED photo for `category`, or `None` if the library has none.

    Lookup order: exact `slugify(category)` match -> `general` -> `None`.
    That is the whole chain: the library is the only source of references, so
    nothing outside it is ever reached for.

    There used to be a third tier -- "any other non-empty category,
    alphabetically" -- removed once the tagger started producing SPECIFIC
    tags: it was silent substitution, not fallback (a `products` request that
    matched nothing came back anchored on a portrait). Nearest-tag string
    matching is that same bargain in nicer wrapping and stays rejected: edit
    distance tracks spelling, not subject. A wrong anchor ships; a missing one
    does not, and `general` remains the deliberate, operator-owned catch-all.

    `None` therefore means "there is no photo this image may be anchored on",
    and every caller answers it by NOT generating: the reels beat and the
    social hook keep the post's own hero, the WP hero generates text2image
    with no reference. It is logged with the tag asked for and the tags that
    exist, because an unmatched request is now usually a planner typo or a
    vocabulary gap -- both fixable, neither visible without the log line.

    Within a category, uploads outrank WP-media harvests (`source_rank`) and
    the seeded pick happens inside the best tier only. `seed` is hashed to an
    index so callers passing `f"{idea_id}:{beat_index}"` spread one reel's
    beats across different photos while a re-run reproduces them exactly;
    `seed=""` always takes the first candidate.

    `prefer_mascot` narrows to `shows_mascot` photos within the requested
    category, then falls back to any category that has one (`_any_mascot_photo`).
    A resolved-but-mascot-less anchor is the quiet failure: image2image runs,
    the scene grounds, and the model invents a different dog -- that shipped a
    terrier as the hero of a post about a 50 lb shepherd mix. It is the one
    exception to the no-substitution rule above; see `_any_mascot_photo`.
    """
    by_category = existing_images_by_category(brand_dir)
    wanted = slugify(category or "")

    for slug in (wanted, GENERAL_CATEGORY):
        candidates = by_category.get(slug) if slug else None
        if not candidates:
            continue
        if prefer_mascot:
            with_mascot = [c for c in candidates if c[1].shows_mascot]
            if with_mascot:
                return pick(best_tier(with_mascot), seed)
            fallback = _any_mascot_photo(by_category, seed)
            if fallback is not None:
                logger.warning(
                    "reference_library_mascot_fallback",
                    requested=slug,
                    anchored_on=fallback.category,
                )
                return fallback
        return pick(best_tier(candidates), seed)
    if prefer_mascot:
        fallback = _any_mascot_photo(by_category, seed)
        if fallback is not None:
            logger.warning(
                "reference_library_mascot_fallback",
                requested=wanted or "(none)",
                anchored_on=fallback.category,
            )
            return fallback
    # Warning, not info: an unanchored generation looks successful and only
    # reveals itself in the finished image, so this needs to be visible
    # without going looking for it.
    logger.warning(
        "reference_library_no_match",
        requested=wanted or "(none)",
        available=",".join(sorted(by_category)),
    )
    return None


def _any_mascot_photo(
    by_category: dict[str, list[Candidate]], seed: str
) -> ReferenceImage | None:
    """Any photo in the library that shows the mascot, or None.

    The cross-category substitution this module otherwise refuses, allowed
    ONLY under `prefer_mascot`. The bargain differs once a caller says the
    subject IS the mascot: the alternative is not "no anchor" but "an anchor
    without her in it", which yields a confidently wrong dog. A portrait in
    the wrong setting is the lesser error, and unlike an invented dog it is
    obvious enough to notice. Most brands will not have a mascot photo in
    every collection, so this is the normal path.

    Mascot-only collections rank first, so a turnaround sheet beats a trail
    photo that merely happens to include her; ties break on name for
    determinism.
    """
    ranked = sorted(
        by_category.items(),
        key=lambda kv: (
            -sum(1 for c in kv[1] if c[1].shows_mascot) / max(len(kv[1]), 1),
            kv[0],
        ),
    )
    for _slug, candidates in ranked:
        with_mascot = [c for c in candidates if c[1].shows_mascot]
        if with_mascot:
            return pick(best_tier(with_mascot), seed)
    return None
