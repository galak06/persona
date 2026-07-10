"""Migrate published_recipes.json → recipes.db content_status column.

Reads state/published_recipes.json (each entry has a ``slug`` field that maps
to the ``id`` column in recipes.db) and marks the corresponding row as
``content_status = 'published'``.

Safe to run multiple times — already-published rows are left untouched.

Usage::

    python -m scripts.migrate_published_json_to_db          # live run
    python -m scripts.migrate_published_json_to_db --dry-run  # preview only
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from recipe_db import db
from recipe_db.models import ContentStatus
from recipe_db.repository import RecipeRepository

logger = logging.getLogger("migrate_published_json_to_db")

# Path of state/published_recipes.json relative to the recipe-publisher root.
_PUBLISHED_JSON_REL: Path = Path("state") / "published_recipes.json"
# recipe-publisher root is two levels above this script file (scripts/…)
_RECIPE_PUBLISHER_ROOT: Path = Path(__file__).resolve().parent.parent


def _load_published_json(json_path: Path) -> list[dict[str, Any]]:
    """Read and parse published_recipes.json; return list of entry dicts."""
    raw = json_path.read_text(encoding="utf-8")
    import json as _json
    data = _json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(
            f"Expected a JSON array in {json_path}, got {type(data).__name__}"
        )
    return data  # type: ignore[return-value]


def _resolve_json_path() -> Path:
    """Return the absolute path to published_recipes.json."""
    return _RECIPE_PUBLISHER_ROOT / _PUBLISHED_JSON_REL


def migrate(dry_run: bool) -> int:
    """Run the migration and return an exit code (0 = success).

    Args:
        dry_run: When True, log what would change but do not commit any writes.

    Returns:
        0 on success, 1 on unrecoverable error.
    """
    json_path = _resolve_json_path()
    if not json_path.exists():
        logger.error("published_recipes.json not found at %s", json_path)
        return 1

    entries = _load_published_json(json_path)
    total_in_json = len(entries)
    logger.info("loaded %d entries from %s", total_in_json, json_path)

    conn = db.connect()
    db.migrate(conn)
    repo = RecipeRepository(conn)

    already_published = 0
    updated = 0
    not_found_in_db = 0

    try:
        for entry in entries:
            slug: str = entry.get("slug", "")
            if not slug:
                logger.warning("entry missing slug field: %s", entry)
                not_found_in_db += 1
                continue

            recipe = repo.get_recipe(slug)
            if recipe is None:
                logger.warning("slug not found in DB: %s", slug)
                not_found_in_db += 1
                continue

            if recipe.content_status == ContentStatus.PUBLISHED:
                logger.debug("already published, skipping: %s", slug)
                already_published += 1
                continue

            # Needs update.
            if dry_run:
                logger.info(
                    "[dry-run] would set content_status=published for slug=%s"
                    " (current=%s)",
                    slug,
                    recipe.content_status,
                )
            else:
                conn.execute(
                    "UPDATE recipes SET content_status = ? WHERE id = ?",
                    (ContentStatus.PUBLISHED, slug),
                )
                logger.info(
                    "updated content_status=published for slug=%s (was=%s)",
                    slug,
                    recipe.content_status,
                )
            updated += 1

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    mode_label = "[DRY-RUN] " if dry_run else ""
    logger.info(
        "%ssummary: total_in_json=%d already_published=%d updated=%d not_found_in_db=%d",
        mode_label,
        total_in_json,
        already_published,
        updated,
        not_found_in_db,
    )
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate published_recipes.json → recipes.db content_status."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without committing any writes.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
