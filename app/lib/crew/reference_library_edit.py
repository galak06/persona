"""Editing an image that is already in the library.

Filing an image is `lib.crew.reference_library_store`; this is the other
half -- changing what the library says about a photo it already holds. It is
a separate module because the store is at its 300-line ceiling, and because
re-tagging is a genuinely different operation: it MOVES bytes, where every
writer over there only ever adds them.

The move is the whole subtlety. An image's id is `"<category>/<filename>"`,
so "change the category" is not a field edit -- it re-homes the file and
renames the id every consumer holds:

* The bytes move with `os.replace` between two directories inside the same
  library root (one filesystem, so the move is atomic; no reader sees a
  partial file). A source file that has already vanished is logged and
  tolerated -- the manifest is the record, and an entry whose file was
  hand-deleted must still be re-taggable.
* Filenames are content-addressed, so an id that already exists at the
  destination means the destination already holds these exact bytes under
  this exact tag. That is a MERGE: the moved entry is dropped and the
  resident one is returned, rather than two entries claiming one file.
* The destination category is auto-declared (`ensure_category`), because the
  operator re-tagging a photo into a label the vision pass proposed should
  not have to declare it first.

Everything runs inside the store's `locked_json` read-modify-write, so a
concurrent upload cannot lose the edit (or vice versa), and a raised error
leaves the manifest untouched.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from lib.crew.reference_library import (
    Manifest,
    empty_manifest,
    library_root,
    manifest_path,
    slugify,
)
from lib.crew.reference_library_store import (
    ensure_category,
    migrate_legacy_dirname,
    normalize_manifest,
    utc_now,
)
from lib.io.jsonio import locked_json
from lib.observability import get_logger

logger = get_logger(__name__)


def update_image(
    brand_dir: Path,
    image_id: str,
    *,
    category: str | None = None,
    shows_mascot: bool | None = None,
    shows_persona: bool | None = None,
    description: str | None = None,
) -> dict[str, Any] | None:
    """Re-tag and/or re-flag one image. Returns its entry, or `None`.

    Two callers, one mover: the operator's `PATCH .../images/{id}` and the
    bulk re-tagger (`lib.crew.reference_retag`), which applies a fresh vision
    answer to a photo already on disk. The re-tagger writes all four fields at
    once; the PATCH writes whichever the operator touched.

    Args:
        brand_dir: The brand root; the library lives under `data/assets`.
        image_id: `"<category>/<filename>"`, as the manifest spells it.
        category: New tag, slugified. `None` (and anything that slugifies to
            nothing) leaves the tag alone -- a PATCH omits what it does not
            change.
        shows_mascot: New per-image mascot flag. `None` leaves it alone.
        shows_persona: New per-image persona flag, judged independently of the
            mascot one. `None` leaves it alone.
        description: New one-line description. `None` leaves it alone; `""`
            deliberately CAN clear it, which is what a re-tag whose model
            returned no sentence should do.

    Returns:
        The updated entry -- carrying its NEW id when the category changed,
        or the resident entry it merged into -- or `None` when the library
        has no such id.
    """
    migrate_legacy_dirname(brand_dir)
    root = library_root(brand_dir)
    with locked_json(manifest_path(brand_dir), empty_manifest()) as manifest:
        normalize_manifest(manifest)
        entry = next((i for i in manifest["images"] if i.get("id") == image_id), None)
        if entry is None:
            logger.warning("reference_library_update_unknown_id", image_id=image_id[:200])
            return None

        slug = slugify(category or "")
        target = entry
        if slug and slug != str(entry.get("category") or ""):
            target = _retag(manifest, entry, slug, label=(category or slug).strip(), root=root)
        if shows_mascot is not None:
            target["shows_mascot"] = bool(shows_mascot)
        if shows_persona is not None:
            target["shows_persona"] = bool(shows_persona)
        if description is not None:
            target["description"] = str(description)
        updated = dict(target)

    logger.info(
        "reference_library_image_updated", image_id=image_id, new_id=str(updated.get("id") or "")
    )
    return updated


def _retag(
    manifest: Manifest, entry: dict[str, Any], slug: str, *, label: str, root: Path
) -> dict[str, Any]:
    """Move `entry` into `slug` and return whichever entry now owns the file.

    That is `entry` itself in the ordinary case, and the RESIDENT entry when
    the destination already carried this id -- see the merge note above.
    """
    filename = str(entry.get("filename") or "")
    new_id = f"{slug}/{filename}"
    _move_bytes(root / str(entry.get("category") or "") / filename, root / slug / filename)
    ensure_category(manifest, slug, label, utc_now())

    resident: dict[str, Any] | None = next(
        (i for i in manifest["images"] if i is not entry and i.get("id") == new_id), None
    )
    if resident is not None:
        manifest["images"] = [i for i in manifest["images"] if i is not entry]
        logger.info("reference_library_retag_merged", image_id=str(entry.get("id")), into=new_id)
        return resident

    entry["id"] = new_id
    entry["category"] = slug
    return entry


def _move_bytes(source: Path, destination: Path) -> None:
    """`os.replace` one stored image between category directories.

    A missing source is a warning, not a failure: the manifest is the record
    of what the library holds, and a re-tag must still succeed for an entry
    whose file went missing underneath it. Any other `OSError` propagates,
    which -- because this runs inside `locked_json` -- abandons the manifest
    edit rather than leaving an entry pointing at a file that never moved.
    """
    if source == destination:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, destination)
    except FileNotFoundError:
        logger.warning("reference_library_move_source_missing", source=str(source))
