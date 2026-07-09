# pyright: reportMissingImports=false, reportMissingModuleSource=false
# (the PostToolUse hook type-checks a /tmp copy where sibling modules + the
#  project venv aren't on the path; resolve those diagnostics inline.)
"""Enrich recipe DB rows with *links* to their on-disk media (never the bytes).

For every recipe the script scans its local artifact folders — both
``<BRAND_DIR>/data/media/recipe_artifacts/<id>/`` and the pre-migration
``<BRAND_DIR>/data/media/_migrated_backup/<id>/`` — and records the images, reels
(video), and audio it finds as a media manifest inside the row's
``generated_content`` JSON. Nothing is copied into the DB and nothing on disk is
deleted: the 532 KB DB stays small while learning *where* its ~150 MB of media
lives.

The manifest is stored as a JSON string under ``generated_content["media"]`` so
the column keeps its ``dict[str, str]`` shape. ``hero_image_url`` is left alone
on purpose — it holds the remote *source* photo URL, not a local path; the
local featured image is exposed via the manifest's ``featured_image`` key.

Idempotent: the manifest is rebuilt from disk (sorted, deduped) every run, so
re-running with no filesystem changes is a no-op.

Run::

    BRAND_DIR=/path/to/persona \\
      python recipe-publisher/scripts/enrich_media_refs.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECIPE_PUBLISHER = HERE.parent
if str(RECIPE_PUBLISHER) not in sys.path:
    sys.path.insert(0, str(RECIPE_PUBLISHER))

from recipe_db import db  # noqa: E402
from recipe_db.repository import RecipeRepository  # noqa: E402

logger = logging.getLogger("enrich_media_refs")

# Media classification by lowercase suffix. Everything else (html/json/md/txt,
# .DS_Store, …) is ignored — those are derived/source-of-truth-elsewhere files.
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}

# Folders (relative to BRAND_DIR) that hold per-recipe media, keyed by slug.
SOURCE_DIRS = ("data/media/recipe_artifacts", "data/media/_migrated_backup")


def _brand_dir() -> Path:
    """Resolve BRAND_DIR; the DB and all media paths hang off it."""
    import os

    brand = os.environ.get("BRAND_DIR")
    if not brand:
        raise SystemExit(
            "BRAND_DIR is not set. Point it at the brand data root, e.g.\n"
            "  BRAND_DIR=/path/to/your/brand-dir"
        )
    path = Path(brand).resolve()
    if not (path / "data").is_dir():
        raise SystemExit(f"BRAND_DIR has no data/ dir: {path}")
    return path


def _scan_recipe_media(brand_dir: Path, recipe_id: str) -> dict[str, object]:
    """Build a media manifest of BRAND_DIR-relative paths for one recipe.

    Returns a dict with sorted, deduped ``images``/``reels``/``audio`` lists and
    a best-guess ``featured_image``. Empty lists are kept so the shape is stable.
    """
    images: set[str] = set()
    reels: set[str] = set()
    audio: set[str] = set()

    for rel_root in SOURCE_DIRS:
        folder = brand_dir / rel_root / recipe_id
        if not folder.is_dir():
            continue
        for file in folder.rglob("*"):
            if not file.is_file():
                continue
            rel = file.relative_to(brand_dir).as_posix()
            ext = file.suffix.lower()
            if ext in IMAGE_EXTS:
                images.add(rel)
            elif ext in VIDEO_EXTS:
                reels.add(rel)
            elif ext in AUDIO_EXTS:
                audio.add(rel)

    manifest: dict[str, object] = {
        "images": sorted(images),
        "reels": sorted(reels),
        "audio": sorted(audio),
    }
    featured = _pick_featured(sorted(images))
    if featured:
        manifest["featured_image"] = featured
    return manifest


def _pick_featured(images: list[str]) -> str | None:
    """Choose the canonical featured image.

    Prefers the live ``recipe_artifacts`` folder over the ``_migrated_backup``
    copy, and within each a file named featured.*, then hero.*, else the first.
    """
    live = [p for p in images if "_migrated_backup" not in p]
    backup = [p for p in images if "_migrated_backup" in p]
    for pool in (live, backup):
        for needle in ("/featured.", "/hero."):
            for path in pool:
                if needle in path:
                    return path
        if pool:
            return pool[0]
    return None


def _manifest_is_empty(manifest: dict[str, object]) -> bool:
    return not (manifest["images"] or manifest["reels"] or manifest["audio"])


def enrich(dry_run: bool, only_id: str | None) -> int:
    """Scan media and write manifests. Returns the number of rows changed."""
    brand_dir = _brand_dir()
    logger.info("BRAND_DIR=%s", brand_dir)

    conn = db.connect()
    try:
        repo = RecipeRepository(conn)
        recipes = repo.list_recipes()
        if only_id:
            recipes = [r for r in recipes if r.id == only_id]
            if not recipes:
                raise SystemExit(f"No recipe with id={only_id!r}")

        changed = 0
        for row in recipes:
            manifest = _scan_recipe_media(brand_dir, row.id)
            new_media = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
            old_media = row.generated_content.get("media", "")

            counts = (
                f"{len(manifest['images'])} img / "  # type: ignore[arg-type]
                f"{len(manifest['reels'])} reels / "  # type: ignore[arg-type]
                f"{len(manifest['audio'])} audio"  # type: ignore[arg-type]
            )

            if _manifest_is_empty(manifest):
                logger.debug("%-45s no media on disk — skipped", row.id)
                continue
            if new_media == old_media:
                logger.info("%-45s up to date (%s)", row.id, counts)
                continue

            logger.info(
                "%-45s %s (%s)", row.id, "WOULD UPDATE" if dry_run else "UPDATED", counts
            )
            if not dry_run:
                updated = dict(row.generated_content)
                updated["media"] = new_media
                # Preserve the existing content-lifecycle state; this is a media
                # backfill, not a draft-content advance.
                repo.set_generated_content(row.id, updated, row.content_status)
            changed += 1

        verb = "would change" if dry_run else "changed"
        logger.info("Done: %d/%d rows %s", changed, len(recipes), verb)
        return changed
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing to the DB",
    )
    parser.add_argument(
        "--id", dest="only_id", default=None, help="enrich a single recipe id only"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="log per-recipe debug detail"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    enrich(dry_run=args.dry_run, only_id=args.only_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
