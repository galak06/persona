# pyright: reportMissingImports=false, reportMissingModuleSource=false
# (the PostToolUse hook type-checks a /tmp copy where sibling modules + the
#  project venv aren't on the path; resolve those diagnostics inline.)
"""Export recipe HTML pages for recipes that are ready (WP published + PDF on disk).

Queries recipes.db for candidates, calls pipeline.html_export, and appends each
result to <BRAND_DIR>/data/html_exports/manifest.json.

Run::

    BRAND_DIR=/path/to/persona \\
      python recipe-publisher/scripts/export_recipe_html.py
    BRAND_DIR=/path/to/persona \\
      python recipe-publisher/scripts/export_recipe_html.py --id peanut-butter-and-banana-dog-biscuits
    BRAND_DIR=/path/to/persona \\
      python recipe-publisher/scripts/export_recipe_html.py --all
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECIPE_PUBLISHER = HERE.parent
RECIPE_PAGE_DIR = RECIPE_PUBLISHER / "templates" / "recipe_page"
for _p in (RECIPE_PUBLISHER, RECIPE_PAGE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from pipeline.html_export import can_export, export_html  # noqa: E402
from recipe_db import db  # noqa: E402
from recipe_db.models import RecipeRow  # noqa: E402
from recipe_db.repository import RecipeRepository  # noqa: E402

logger = logging.getLogger(__name__)

_MANIFEST_REL = Path("data/html_exports/manifest.json")


def _brand_dir() -> Path:
    brand_dir = os.environ.get("BRAND_DIR")
    if brand_dir:
        return Path(brand_dir)
    return RECIPE_PUBLISHER.parent.parent / "persona"


def _db_path() -> Path:
    branded = _brand_dir() / "data" / "db" / "recipes.db"
    return branded if branded.exists() else RECIPE_PUBLISHER / "data" / "db" / "recipes.db"


def _associates_tag() -> str:
    return os.environ.get("AMAZON_ASSOCIATES_TAG", "").strip()


def _append_manifest(brand_dir: Path, recipe: RecipeRow, html_path: Path) -> None:
    """Append one export entry to <BRAND_DIR>/data/html_exports/manifest.json."""
    manifest_path = brand_dir / _MANIFEST_REL
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, str]] = []
    if manifest_path.exists():
        try:
            entries = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            entries = []

    # Store html_path relative to brand_dir for portability.
    try:
        rel = html_path.relative_to(brand_dir)
    except ValueError:
        rel = html_path

    entries.append(
        {
            "id": recipe.id,
            "title": recipe.display_name or recipe.name,
            "html_path": str(rel),
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    manifest_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _candidates_pending(repo: RecipeRepository, brand_dir: Path) -> list[RecipeRow]:
    """Recipes where html_exported_at IS NULL and can_export() passes."""
    cur = repo._conn.execute(  # noqa: SLF001
        "SELECT * FROM recipes WHERE html_exported_at IS NULL ORDER BY id"
    )
    rows = [RecipeRepository._row_to_recipe(r) for r in cur.fetchall()]  # noqa: SLF001
    return [r for r in rows if can_export(r, brand_dir)]


def _candidates_all(repo: RecipeRepository, brand_dir: Path) -> list[RecipeRow]:
    """All recipes that pass can_export() (regardless of html_exported_at)."""
    return [r for r in repo.list_recipes() if can_export(r, brand_dir)]


def _process_row(
    recipe: RecipeRow,
    brand_dir: Path,
    repo: RecipeRepository,
    *,
    skip_can_export_check: bool = False,
) -> tuple[bool, str]:
    """Export one recipe. Returns (exported: bool, reason_if_skipped: str)."""
    if not skip_can_export_check and not can_export(recipe, brand_dir):
        wp_ok = recipe.publish_status.get("wp", {}).get("state") == "published"
        reason = "not wp-published" if not wp_ok else "no pdf"
        return False, reason

    try:
        html_path = export_html(recipe, brand_dir, repo, associates_tag=_associates_tag())
        _append_manifest(brand_dir, recipe, html_path)
        return True, str(html_path)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description="Export recipe HTML pages for WP-published + PDF-ready recipes."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--pending",
        action="store_true",
        default=False,
        help="(default) recipes where html_exported_at IS NULL AND can_export() passes",
    )
    group.add_argument(
        "--id",
        metavar="RECIPE_ID",
        help="export a single recipe by slug (re-exports even if already exported)",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="all recipes that pass can_export() (re-exports already exported ones)",
    )
    args = parser.parse_args()

    brand_dir = _brand_dir()
    conn = db.connect(_db_path())
    exported_count = 0
    skipped_count = 0

    try:
        db.migrate(conn)
        repo = RecipeRepository(conn)

        if args.id:
            # Single recipe — always re-export, skip html_exported_at check.
            recipe = repo.get_recipe(args.id)
            if recipe is None:
                logger.error("recipe not in DB: %s", args.id)
                sys.exit(1)
            ok, detail = _process_row(recipe, brand_dir, repo, skip_can_export_check=False)
            if ok:
                logger.info("exported: %s → %s", recipe.id, detail)
                exported_count += 1
            else:
                logger.info("skipped:  %s (reason: %s)", recipe.id, detail)
                skipped_count += 1

        elif args.all:
            candidates = _candidates_all(repo, brand_dir)
            for recipe in candidates:
                ok, detail = _process_row(recipe, brand_dir, repo, skip_can_export_check=True)
                if ok:
                    logger.info("exported: %s → %s", recipe.id, detail)
                    exported_count += 1
                else:
                    logger.info("skipped:  %s (reason: %s)", recipe.id, detail)
                    skipped_count += 1

        else:
            # Default: --pending
            candidates = _candidates_pending(repo, brand_dir)
            for recipe in candidates:
                ok, detail = _process_row(recipe, brand_dir, repo, skip_can_export_check=True)
                if ok:
                    logger.info("exported: %s → %s", recipe.id, detail)
                    exported_count += 1
                else:
                    logger.info("skipped:  %s (reason: %s)", recipe.id, detail)
                    skipped_count += 1

    finally:
        conn.close()

    logger.info("done: %d exported, %d skipped", exported_count, skipped_count)


if __name__ == "__main__":
    main()
