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


def descriptions_by_category(brand_dir: Path) -> dict[str, list[str]]:
    """Each stocked category's slug -> the vision descriptions of its photos.

    Feeds `lib.crew.reference_vocabulary.described_categories`, so a planner
    choosing a collection is shown what that collection actually CONTAINS
    rather than only its slug. Best photos first, matching `resolve_reference`'s
    ranking, so the examples a planner reads are the ones most likely to be the
    photo it gets. Entries with no description are skipped rather than padded --
    an empty list degrades that one category to its bare label.
    """
    described: dict[str, list[str]] = {}
    for slug, candidates in existing_images_by_category(brand_dir).items():
        texts = [
            image.description.strip()
            for _, image in sorted(candidates, key=lambda c: c[0])
            if image.description and image.description.strip()
        ]
        if texts:
            described[slug] = texts
    return described


def resolve_reference(
    brand_dir: Path, category: str | None, *, seed: str = ""
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

    A resolved-but-mascot-less photo is not this function's problem to solve:
    grounding the mascot is a SECOND reference attached alongside the scene
    (`lib.crew.reference_mascot.mascot_anchor` + `paired_reference_clause`),
    not a different choice of scene. Replacing the scene to smuggle the mascot
    in loses the setting the planner asked for and was briefly tried here.
    """
    by_category = existing_images_by_category(brand_dir)
    wanted = slugify(category or "")

    for slug in (wanted, GENERAL_CATEGORY):
        candidates = by_category.get(slug) if slug else None
        if candidates:
            return pick(best_tier(candidates), seed)
    # Warning, not info: an unanchored generation looks successful and only
    # reveals itself in the finished image, so this needs to be visible
    # without going looking for it.
    logger.warning(
        "reference_library_no_match",
        requested=wanted or "(none)",
        available=",".join(sorted(by_category)),
    )
    return None
