"""Tagged mascot reference-image library -- READ side.

A brand may keep several real photos of its persona/mascot, tagged by the
kind of scene they show (`eating`, `walking`, ..., plus a catch-all
`general`). Image generators pick the reference whose tag matches the beat
they are about to render, instead of conditioning every frame on the one
legacy `persona_mascot_reference.png`.

Layout under `$BRAND_DIR`::

    data/assets/
      persona_mascot_reference.png   # LEGACY -- never touched, never copied
      mascot_refs/
        library.json                 # manifest (see `read_manifest`)
        general/<sha256[:16]><ext>
        eating/<sha256[:16]><ext>

Filenames are content-addressed, so the same bytes in the same category are
one file; the uploader's filename never reaches the filesystem (it survives
as the manifest entry's `label`).

This module is imported by the worker on every generated beat, so it is
deliberately dependency-light: **no Pillow, no FastAPI**. Byte validation
lives in `lib.crew.mascot_validate` (the only PIL importer) and writes in
`lib.crew.mascot_library_store`. The import direction is one-way --
`lib.crew.wp_image` imports from here, never the reverse.

Every read is tolerant: a missing or malformed manifest reads as an empty
library, an entry whose file vanished is skipped with a warning, a stray
file with no entry is ignored. A brand with no library keeps the legacy
behaviour; a brand with neither gets `None` -- "generate without a
reference", not an error.
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

LIBRARY_DIRNAME = "mascot_refs"
GENERAL_CATEGORY = "general"
LEGACY_CATEGORY = "legacy"
MANIFEST_FILENAME = "library.json"
MANIFEST_VERSION = 1

LEGACY_REFERENCE_STEM = "persona_mascot_reference"
LEGACY_REFERENCE_EXTENSIONS = (".png", ".jpg", ".jpeg")

#: Canonical extension -> content type. Only the legacy file needs it (it
#: has no manifest entry); library images carry their sniffed type.
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
    category: str  # resolved slug, or "legacy"
    path: Path
    content_type: str
    label: str


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
    """`$BRAND_DIR/data/assets/mascot_refs`. May not exist."""
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
        logger.warning("mascot_library_manifest_unreadable", path=str(path), error=str(exc))
        return empty_manifest()
    if not isinstance(raw, dict):
        if raw is not None:
            logger.warning("mascot_library_manifest_not_an_object", path=str(path))
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
    """Best reference photo for `category`, or `None` if the brand has none.

    Lookup order: exact `slugify(category)` match -> `general` -> any other
    non-empty category (alphabetical, so the choice is reproducible) ->
    the legacy `persona_mascot_reference.{png,jpg,jpeg}` -> `None`.

    Within a category, uploads outrank WP-media harvests (`source_rank`) and
    the seeded pick happens inside the best tier only. `seed` is hashed to an
    index so callers passing `f"{idea_id}:{beat_index}"` spread one reel's
    beats across different photos while a re-run reproduces them exactly;
    `seed=""` always takes the first candidate.
    """
    by_category = _existing_images_by_category(brand_dir)
    wanted = slugify(category or "")

    ordered_slugs = [wanted, GENERAL_CATEGORY, *sorted(by_category)]
    for slug in ordered_slugs:
        candidates = by_category.get(slug) if slug else None
        if candidates:
            return _pick(_best_tier(candidates), seed)

    legacy = resolve_reference_image_path(brand_dir)
    if legacy is not None:
        return ReferenceImage(
            id=legacy.name,
            category=LEGACY_CATEGORY,
            path=legacy,
            content_type=CONTENT_TYPE_BY_SUFFIX.get(legacy.suffix.lower(), _FALLBACK_CONTENT_TYPE),
            label=legacy.stem,
        )
    return None


def resolve_reference_image_path(brand_dir: Path) -> Path | None:
    """The brand's optional LEGACY persona+mascot reference photo, if one
    exists -- `$BRAND_DIR/data/assets/persona_mascot_reference.{png,jpg,jpeg}`.

    Moved here from `lib.crew.wp_image` (which now imports it back as a
    delegating alias) so the legacy probe and the tagged library share one
    home. `None` means "generate without a reference" -- the pre-existing,
    generic behaviour -- not an error: this pipeline is brand-agnostic and
    most brands have no such asset.
    """
    directory = assets_dir(brand_dir)
    for ext in LEGACY_REFERENCE_EXTENSIONS:
        candidate = directory / f"{LEGACY_REFERENCE_STEM}{ext}"
        if candidate.is_file():
            return candidate
    return None


def identity_clause(mascot_name: str) -> str:
    """The subject-consistency instruction appended to an image prompt when a
    reference photo IS attached.

    Moved verbatim out of `lib.crew.wp_image._style_suffix` so every
    generator that conditions on a library photo phrases the constraint
    identically.
    """
    return (
        "A reference photo of the brand's real persona and mascot is attached -- use the "
        "EXACT SAME person and dog shown in that photo (same face, same dog coloring/"
        f"markings{f' -- the dog is {mascot_name}' if mascot_name else ''}), placed into "
        "this new scene. Do not invent a different person or dog. "
    )


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
                "mascot_library_entry_file_missing", image_id=str(entry.get("id")), path=str(path)
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
