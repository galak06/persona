# pyright: reportMissingImports=false, reportMissingModuleSource=false
# (the PostToolUse hook type-checks a /tmp copy where sibling modules + the
#  project venv aren't on the path; resolve those diagnostics inline.)
"""Render a full recipe PAGE (HTML+CSS) straight from ``recipes.db`` rows.

Bridges the recipe DB (fields) + on-disk image artifacts (featured.jpg, the
rendered recipe card, carousel slides) to the page renderer, then writes
``recipe_page.html`` into each recipe's artifact folder so it can be opened and
verified — locally or via the web frontend — before anything is published.

Run::

    BRAND_DIR=/path/to/dogfoodandfun \\
      python recipe-publisher/scripts/render_page_from_db.py --id peanut-butter-and-banana-dog-biscuits
    BRAND_DIR=/path/to/dogfoodandfun \\
      python recipe-publisher/scripts/render_page_from_db.py --all
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECIPE_PUBLISHER = HERE.parent
RECIPE_PAGE_DIR = RECIPE_PUBLISHER / "templates" / "recipe_page"
for _p in (RECIPE_PUBLISHER, RECIPE_PAGE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from page_from_db import page_data_from_row  # noqa: E402
from page_render import build_page_html  # noqa: E402
from recipe_db import db  # noqa: E402
from recipe_db.models import RecipeRow  # noqa: E402
from recipe_db.repository import RecipeRepository  # noqa: E402

logger = logging.getLogger(__name__)

_PAGE_NAME = "recipe_page.html"


def _brand_dir() -> Path:
    brand_dir = os.environ.get("BRAND_DIR")
    if brand_dir:
        return Path(brand_dir)
    return RECIPE_PUBLISHER.parent / "dogfoodandfun"


def _db_path() -> Path:
    branded = _brand_dir() / "data" / "db" / "recipes.db"
    return branded if branded.exists() else RECIPE_PUBLISHER / "data" / "db" / "recipes.db"


def _render_row(repo: RecipeRepository, brand_dir: Path, row: RecipeRow) -> Path:
    """Render + persist one recipe's page; point its artifacts_path at the folder.

    Image refs are written as paths relative to the HTML file (``images/...``)
    so the saved artifact opens correctly straight off disk.
    """
    artifacts_rel = f"data/media/recipe_artifacts/{row.id}"
    artifacts_dir = brand_dir / artifacts_rel
    data = page_data_from_row(
        row,
        artifacts_dir / "images",
        ref=lambda rel: rel,
        associates_tag=os.environ.get("AMAZON_ASSOCIATES_TAG", "").strip(),
    )
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_path = artifacts_dir / _PAGE_NAME
    out_path.write_text(build_page_html(data), encoding="utf-8")
    repo.set_artifacts_path(row.id, artifacts_rel)
    return out_path


def render_page_from_db(recipe_id: str) -> Path:
    """Render the page for ``recipe_id`` and write it into the artifact folder."""
    conn = db.connect(_db_path())
    try:
        db.migrate(conn)
        repo = RecipeRepository(conn)
        row = repo.get_recipe(recipe_id)
        if row is None:
            raise KeyError(f"recipe not in DB: {recipe_id}")
        return _render_row(repo, _brand_dir(), row)
    finally:
        conn.close()


def render_all() -> list[tuple[str, Path | None, str | None]]:
    """Render every recipe in the DB; return (id, path, error) per recipe."""
    conn = db.connect(_db_path())
    results: list[tuple[str, Path | None, str | None]] = []
    try:
        db.migrate(conn)
        repo = RecipeRepository(conn)
        brand_dir = _brand_dir()
        for row in repo.list_recipes():
            try:
                results.append((row.id, _render_row(repo, brand_dir, row), None))
            except Exception as exc:  # noqa: BLE001 — keep batch going, report per row
                results.append((row.id, None, str(exc)))
    finally:
        conn.close()
    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Render recipe page(s) from the DB.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", help="recipe id (slug) in recipes.db")
    group.add_argument("--all", action="store_true", help="render every recipe")
    args = parser.parse_args()

    if args.all:
        results = render_all()
        ok = [r for r in results if r[2] is None]
        for rid, _path, err in results:
            logger.info("  %s %s", "OK " if err is None else "ERR", rid + (f" — {err}" if err else ""))
        logger.info("rendered %d/%d recipe pages", len(ok), len(results))
    else:
        logger.info("recipe page saved: %s", render_page_from_db(args.id))


if __name__ == "__main__":
    main()
