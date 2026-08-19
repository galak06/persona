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

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.io.jsonio import read_json
from lib.observability import get_logger

logger = get_logger(__name__)

LIBRARY_DIRNAME = "reference_images"
GENERAL_CATEGORY = "general"
MANIFEST_FILENAME = "library.json"
MANIFEST_VERSION = 1

#: Canonical extension -> content type, for images whose entry carries none:
#: a hand-edited manifest here, the legacy asset on `import_legacy`'s path.
CONTENT_TYPE_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_FALLBACK_CONTENT_TYPE = "image/png"

#: Hand-uploaded photos are user-curated ground truth; WP-media harvests
#: were machine-tagged and merely approved. Uploads therefore win outright:
#: `resolve_reference` picks *within the best-ranked tier a category holds*,
#: so a harvest surfaces only where there is no upload at all.
SOURCE_PRIORITY: dict[str, int] = {"upload": 0, "wp_media": 1}
_UNKNOWN_SOURCE_RANK = 2

#: `{"version": 1, "categories": [...], "images": [...]}` -- kept a plain
#: dict so the store can mutate it in place inside `locked_json`.
Manifest = dict[str, Any]


@dataclass(frozen=True)
class ReferenceImage:
    """One resolved reference photo, ready to read off disk."""

    id: str
    category: str  # resolved slug
    path: Path
    content_type: str
    label: str
    #: Does this photo actually show the brand's mascot? Decides which prompt
    #: clause a generator attaches (`lib.crew.reference_clauses`). Entries
    #: written before the vision tagger have no such key, so the default is
    #: the SAFE reading: assume it is not a mascot portrait.
    shows_mascot: bool = False
    #: What the tagger saw, carried for logs and operator UI only -- never
    #: sent to the image model.
    description: str = ""
    #: Does it show the brand's own PERSONA -- the person behind it? Judged
    #: independently of `shows_mascot` (a photo may show either, both, or
    #: neither), and defaulted the same defensive way for the same reason.
    shows_persona: bool = False


def slugify(label: str) -> str:
    """Lowercase, hyphenate, strip non-alphanumerics -- same shape as
    `lib.groups_db.models.slugify`."""
    return re.sub(r"[^a-z0-9]+", "-", (label or "").strip().lower()).strip("-")


def source_rank(source: str) -> int:
    """Priority of an image's `source` (lower wins). Shared by the store and
    API phases so every consumer orders candidates identically."""
    return SOURCE_PRIORITY.get(source or "", _UNKNOWN_SOURCE_RANK)


def assets_dir(brand_dir: Path) -> Path:
    """`$BRAND_DIR/data/assets` -- holds both the legacy file and the library."""
    return brand_dir / "data" / "assets"


def library_root(brand_dir: Path) -> Path:
    """`$BRAND_DIR/data/assets/reference_images`. May not exist."""
    return assets_dir(brand_dir) / LIBRARY_DIRNAME


def manifest_path(brand_dir: Path) -> Path:
    """`<library_root>/library.json`. May not exist."""
    return library_root(brand_dir) / MANIFEST_FILENAME


def empty_manifest() -> Manifest:
    """A fresh, valid, empty manifest."""
    return {"version": MANIFEST_VERSION, "categories": [], "images": []}


def read_manifest(brand_dir: Path) -> Manifest:
    """Read `library.json`, normalized and never raising.

    A missing file, unreadable file, malformed JSON, or a JSON document of
    the wrong shape all degrade to `empty_manifest()` -- the library is an
    optional enhancement, and a corrupt manifest must not take down image
    generation. Entries missing the fields the resolver needs are dropped.
    """
    path = manifest_path(brand_dir)
    try:
        raw = read_json(path, None)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("reference_library_manifest_unreadable", path=str(path), error=str(exc))
        return empty_manifest()
    if not isinstance(raw, dict):
        if raw is not None:
            logger.warning("reference_library_manifest_not_an_object", path=str(path))
        return empty_manifest()

    def dicts(key: str) -> list[dict[str, Any]]:
        values = raw.get(key) if isinstance(raw, dict) else None
        return [v for v in values if isinstance(v, dict)] if isinstance(values, list) else []

    categories = [c for c in dicts("categories") if c.get("slug")]
    images = [i for i in dicts("images") if i.get("id") and i.get("category") and i.get("filename")]
    version = raw.get("version")
    return {
        "version": version if isinstance(version, int) else MANIFEST_VERSION,
        "categories": categories,
        "images": images,
    }


def list_category_labels(brand_dir: Path) -> list[str]:
    """Human labels of every declared category, for prompt injection.

    Declared order first (that is the order an operator created them in),
    then any category that only exists on an image entry -- defensive, so a
    hand-edited manifest still advertises its tags.
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
    return list(labels.values())


def resolve_reference(
    brand_dir: Path, category: str | None, *, seed: str = ""
) -> ReferenceImage | None:
    """Best UPLOADED photo for `category`, or `None` if the library has none.

    Lookup order: exact `slugify(category)` match -> `general` -> `None`.
    That is the whole chain: the library is the only source of references, so
    nothing outside it is ever reached for.

    There used to be a third tier -- "any other non-empty category,
    alphabetically" -- removed once the tagger started producing SPECIFIC
    tags. It was never a fallback so much as a silent substitution: a request
    for `products` that matched nothing returned whichever tag sorted first,
    so a product beat came back anchored on a portrait. A wrong anchor ships;
    a missing one does not. `general` survives as the deliberate catch-all, so
    a blanket fallback is still one re-tag away.

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
    """
    by_category = _existing_images_by_category(brand_dir)
    wanted = slugify(category or "")

    for slug in (wanted, GENERAL_CATEGORY):
        candidates = by_category.get(slug) if slug else None
        if candidates:
            return _pick(_best_tier(candidates), seed)
    logger.info(
        "reference_library_no_match",
        requested=wanted or "(none)",
        available=",".join(sorted(by_category)),
    )
    return None


#: One candidate as the resolver carries it internally: its `source` rank
#: paired with the image, so ranking never has to widen `ReferenceImage`.
_Candidate = tuple[int, ReferenceImage]


def _existing_images_by_category(brand_dir: Path) -> dict[str, list[_Candidate]]:
    """Manifest entries grouped by category, dropping any whose file is gone."""
    root = library_root(brand_dir)
    grouped: dict[str, list[_Candidate]] = {}
    for entry in read_manifest(brand_dir)["images"]:
        slug = slugify(str(entry.get("category", "")))
        if not slug:
            continue
        path = root / slug / str(entry.get("filename", ""))
        if not path.is_file():
            logger.warning(
                "reference_library_entry_file_missing",
                image_id=str(entry.get("id")),
                path=str(path),
            )
            continue
        image = ReferenceImage(
            id=str(entry.get("id")),
            category=slug,
            path=path,
            content_type=str(
                entry.get("content_type")
                or CONTENT_TYPE_BY_SUFFIX.get(path.suffix.lower(), _FALLBACK_CONTENT_TYPE)
            ),
            label=str(entry.get("label") or path.stem),
            shows_mascot=bool(entry.get("shows_mascot", False)),
            description=str(entry.get("description") or ""),
            shows_persona=bool(entry.get("shows_persona", False)),
        )
        grouped.setdefault(slug, []).append((source_rank(str(entry.get("source", ""))), image))
    return grouped


def _best_tier(candidates: list[_Candidate]) -> list[ReferenceImage]:
    """Only the highest-priority `source` tier present, sorted by id.

    Uploads never lose to harvested images: with 3 uploads and 5 harvests in
    one category the seeded pick chooses among the 3 uploads, and a harvest
    surfaces only once the category holds no uploads at all. Sorting by id
    (not manifest order) keeps the result stable across manifest re-orderings.
    """
    ranked = sorted(candidates, key=lambda c: (c[0], c[1].id))
    best = ranked[0][0]
    return [image for rank, image in ranked if rank == best]


def _pick(candidates: list[ReferenceImage], seed: str) -> ReferenceImage:
    """Deterministic index from `seed` -- no `random`, so re-runs reproduce."""
    if not seed:
        return candidates[0]
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return candidates[int(digest, 16) % len(candidates)]
