"""Re-run the vision tagger over photos the library already holds.

Uploading is the only time a photo is tagged, which is fine until the TAGGER
changes -- and it just did. A library filed under the old prompt is a library
of one bucket (see `lib.crew.reference_vision_prompt` for why), and the fix
cannot be "re-upload everything": the store is content-addressed, so the same
bytes land on the same id under the same tag and nothing moves.

So this module re-asks the question. For each entry it reads the stored bytes
back off disk, runs `analyze_for_brand` over them exactly as an upload would,
and applies the answer through `lib.crew.reference_library_edit.update_image`
-- the one mover in the codebase, because a new category MOVES the file and
renames the id. Nothing here writes to the filesystem itself.

**Bulk, not per-image.** The defect is library-wide: every photo was tagged
under the same coarse prompt, so the unit of the fix is the library. A
per-image variant would need its own route, its own schema and its own UI
affordance for a case the operator can already reach -- the existing PATCH
lets them retag one photo by hand, and hand-correcting one photo is a
judgement, not a re-run.

**One bad image may not end the run.** Every step is per image and every
failure is caught and recorded: a file that vanished under the manifest, a
byte read that fails, a vision call that returns nothing, an id the library no
longer has. The caller gets one outcome per image and decides what to say
about them. That matters more here than anywhere else in the library, because
a bulk run costs one vision call per photo and quota is real: aborting at
image seven would have spent seven calls and applied none of them.

Ordering is the manifest's own, and each image is analysed against the tags
that exist AT THAT MOMENT (`analyze_for_brand` re-reads them per call), so a
specific tag the third photo coins is offered to the fourth. That is how a
vocabulary accumulates instead of ten near-duplicates appearing at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.crew.reference_library import (
    CONTENT_TYPE_BY_SUFFIX,
    library_root,
    read_manifest,
)
from lib.crew.reference_library_edit import update_image
from lib.crew.reference_library_store import migrate_legacy_dirname
from lib.crew.reference_vision import analyze_for_brand
from lib.observability import get_logger

logger = get_logger(__name__)

_FALLBACK_CONTENT_TYPE = "image/png"

#: What happened to one photo. `retagged` covers any applied change (a new
#: tag, a flipped flag, a fresh description); `unchanged` means the model
#: confirmed what was already there, which is a success, not a no-op worth
#: hiding.
RETAGGED = "retagged"
UNCHANGED = "unchanged"
SKIPPED = "skipped"
FAILED = "failed"


@dataclass(frozen=True)
class RetagOutcome:
    """One photo's result. `new_id` differs from `image_id` after a move."""

    image_id: str
    new_id: str
    status: str
    category: str = ""
    shows_mascot: bool = False
    shows_persona: bool = False
    description: str = ""
    detail: str = ""


def retag_library(brand_dir: Path) -> list[RetagOutcome]:
    """Re-analyse every photo in the brand's library, in manifest order.

    Blocking and slow by nature -- one vision round trip per photo -- so
    callers on an event loop must push it to a threadpool.

    Returns:
        One `RetagOutcome` per manifest entry, in the order they were
        processed. Never raises: a per-image failure becomes a `FAILED`
        outcome and the run continues.
    """
    migrate_legacy_dirname(brand_dir)
    entries = list(read_manifest(brand_dir)["images"])
    logger.info("reference_retag_started", brand_dir=str(brand_dir), images=len(entries))

    outcomes = [_retag_one(brand_dir, entry) for entry in entries]
    logger.info(
        "reference_retag_finished",
        images=len(outcomes),
        retagged=sum(1 for o in outcomes if o.status == RETAGGED),
        failed=sum(1 for o in outcomes if o.status == FAILED),
    )
    return outcomes


def _retag_one(brand_dir: Path, entry: dict[str, Any]) -> RetagOutcome:
    """One photo, start to finish, with every failure caught and reported."""
    image_id = str(entry.get("id") or "")
    category = str(entry.get("category") or "")
    filename = str(entry.get("filename") or "")
    try:
        path = library_root(brand_dir) / category / filename
        data = path.read_bytes()
        content_type = str(entry.get("content_type") or "") or CONTENT_TYPE_BY_SUFFIX.get(
            path.suffix.lower(), _FALLBACK_CONTENT_TYPE
        )
        analysis = analyze_for_brand(brand_dir, data, content_type)
    except OSError as exc:
        logger.warning("reference_retag_unreadable", image_id=image_id, error=str(exc))
        return RetagOutcome(image_id, image_id, FAILED, category, detail=str(exc))
    except Exception as exc:  # one bad photo may not end a paid-for run
        logger.warning("reference_retag_image_failed", image_id=image_id, error=str(exc))
        return RetagOutcome(image_id, image_id, FAILED, category, detail=str(exc))

    if analysis is None:
        # `analyze_for_brand` already logged why; from here it is simply "no
        # answer", and the photo keeps the tag and flags it has.
        return RetagOutcome(
            image_id,
            image_id,
            SKIPPED,
            category,
            shows_mascot=bool(entry.get("shows_mascot")),
            shows_persona=bool(entry.get("shows_persona")),
            description=str(entry.get("description") or ""),
            detail="the vision pass had no answer",
        )

    try:
        updated = update_image(
            brand_dir,
            image_id,
            category=analysis.category,
            shows_mascot=analysis.shows_mascot,
            shows_persona=analysis.shows_persona,
            description=analysis.description,
        )
    except Exception as exc:
        # A failed move abandons this photo's manifest edit (it runs inside
        # `locked_json`) and nothing else -- so the run carries on, exactly as
        # it does for a photo whose bytes could not be read.
        logger.warning("reference_retag_apply_failed", image_id=image_id, error=str(exc))
        return RetagOutcome(image_id, image_id, FAILED, category, detail=str(exc))
    if updated is None:
        return RetagOutcome(
            image_id, image_id, FAILED, category, detail="the library no longer has that image"
        )

    new_id = str(updated.get("id") or image_id)
    moved = new_id != image_id
    changed = (
        moved
        or bool(entry.get("shows_mascot")) != analysis.shows_mascot
        or bool(entry.get("shows_persona")) != analysis.shows_persona
        or str(entry.get("description") or "") != analysis.description
    )
    return RetagOutcome(
        image_id,
        new_id,
        RETAGGED if changed else UNCHANGED,
        str(updated.get("category") or analysis.category),
        shows_mascot=analysis.shows_mascot,
        shows_persona=analysis.shows_persona,
        description=analysis.description,
    )
