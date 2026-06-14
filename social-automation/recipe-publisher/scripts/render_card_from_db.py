# pyright: reportMissingImports=false, reportMissingModuleSource=false
# (the PostToolUse hook type-checks a /tmp copy where sibling modules + the
#  project venv aren't on the path; resolve those diagnostics inline.)
"""Render a split-collage recipe card straight from a ``recipes.db`` row.

This bridges the recipe DB (title + ingredients + source hero photo) to the
existing recipe-card renderer, then saves the PNG as a first-class artifact —
under ``<BRAND_DIR>/data/media/recipe_artifacts/<id>/images/`` alongside the other
recipe assets — and points the DB row's ``artifacts_path`` at that folder so the
web frontend surfaces it like every other artifact.

Run::

    BRAND_DIR=/path/to/dogfoodandfun \\
      python recipe-publisher/scripts/render_card_from_db.py --id peanut-butter-and-banana-dog-biscuits
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import urllib.request
from pathlib import Path

# The renderer + card model live in the (hyphenated, non-importable)
# recipe-publisher tree; add both that dir and the card dir to the path so we
# can reuse the existing renderer and DB repository instead of duplicating them.
HERE = Path(__file__).resolve().parent
RECIPE_PUBLISHER = HERE.parent
RECIPE_CARD_DIR = RECIPE_PUBLISHER / "templates" / "recipe_card"
for _p in (RECIPE_PUBLISHER, RECIPE_CARD_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from recipe_data import RecipeCardData  # noqa: E402
from recipe_db import db  # noqa: E402
from recipe_db.models import Ingredient, RecipeRow  # noqa: E402
from recipe_db.repository import RecipeRepository  # noqa: E402
from render import RenderJob, render_card  # noqa: E402

logger = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (recipe-card-renderer)"
_CARD_NAME = "recipe_card.png"

# Decimals the scraper stores (e.g. 0.33333334326744 cup) → typeset fractions.
_DECIMAL_FRACTIONS: list[tuple[float, str]] = [
    (0.125, "⅛"),  # 1/8
    (0.25, "¼"),   # 1/4
    (0.333, "⅓"),  # 1/3
    (0.375, "⅜"),  # 3/8
    (0.5, "½"),    # 1/2
    (0.625, "⅝"),  # 5/8
    (0.667, "⅔"),  # 2/3
    (0.75, "¾"),   # 3/4
    (0.875, "⅞"),  # 7/8
]
_LEADING_NUMBER = re.compile(r"^(\d+(?:\.\d+)?)(\s+.*)?$")


def _brand_dir() -> Path:
    """Resolve the brand dir from ``BRAND_DIR`` (recipe-publisher convention)."""
    brand_dir = os.environ.get("BRAND_DIR")
    if brand_dir:
        return Path(brand_dir)
    return RECIPE_PUBLISHER.parent / "dogfoodandfun"


def _db_path() -> Path:
    """The brand DB, falling back to the engine copy when BRAND_DIR is unset."""
    branded = _brand_dir() / "data" / "db" / "recipes.db"
    if branded.exists():
        return branded
    return RECIPE_PUBLISHER / "data" / "db" / "recipes.db"


def _humanize_qty(text: str) -> str:
    """Rewrite a leading decimal quantity as a mixed fraction (2.5 -> 2½)."""
    match = _LEADING_NUMBER.match(text.strip())
    if not match:
        return text.strip()
    value = float(match.group(1))
    rest = match.group(2) or ""
    whole = int(value)
    frac = value - whole
    for target, glyph in _DECIMAL_FRACTIONS:
        if abs(frac - target) <= 0.04:
            head = str(whole) if whole else ""
            return f"{head}{glyph}{rest}"
    number = str(whole) if value == whole else f"{value:g}"
    return f"{number}{rest}"


def _ingredient_lines(ingredients: list[Ingredient]) -> list[str]:
    """Turn stored ingredients into clean display lines with fraction glyphs."""
    lines: list[str] = []
    for ing in ingredients:
        text = ing.item or " ".join(
            part for part in (ing.qty, ing.unit, ing.notes) if part
        )
        text = text.strip()
        if text:
            lines.append(_humanize_qty(text))
    return lines


def _ensure_hero(row: RecipeRow, images_dir: Path) -> Path:
    """Return the local hero photo, downloading the source once if missing."""
    images_dir.mkdir(parents=True, exist_ok=True)
    featured = images_dir / "featured.jpg"
    if featured.exists():
        return featured
    if not row.hero_image_url:
        raise ValueError(f"recipe {row.id} has no local hero and no hero_image_url")
    request = urllib.request.Request(  # noqa: S310
        row.hero_image_url, headers={"User-Agent": _USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        payload = response.read()
    tmp = featured.with_suffix(".jpg.part")
    tmp.write_bytes(payload)
    tmp.replace(featured)
    return featured


def render_from_db(recipe_id: str, style: str) -> Path:
    """Render the card for ``recipe_id``, save it as an artifact, return its path.

    Side effects: writes ``recipe_card.png`` into the recipe's artifact images
    folder and sets the DB row's ``artifacts_path`` so the frontend lists it.
    """
    brand_dir = _brand_dir()
    artifacts_rel = f"data/media/recipe_artifacts/{recipe_id}"
    images_dir = brand_dir / artifacts_rel / "images"

    conn = db.connect(_db_path())
    try:
        db.migrate(conn)
        repo = RecipeRepository(conn)
        row = repo.get_recipe(recipe_id)
        if row is None:
            raise KeyError(f"recipe not in DB: {recipe_id}")

        hero = _ensure_hero(row, images_dir)
        card = RecipeCardData(
            seed_id=recipe_id,
            title=row.display_name or row.name or recipe_id,
            ingredients=_ingredient_lines(row.ingredients),
            steps=row.steps,
            prep_minutes=row.prep_minutes,
            cook_minutes=row.cook_minutes,
            yield_servings=row.servings,
            hero_path=hero,
            slide_paths=[],  # one source photo; the grid reuses the hero
        )
        job = RenderJob(style, recipe_id, _CARD_NAME)
        out_path = render_card(job, images_dir, card)

        repo.set_artifacts_path(recipe_id, artifacts_rel)
        repo.set_card(recipe_id, f"{artifacts_rel}/images/{_CARD_NAME}")
    finally:
        conn.close()

    return out_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Render a recipe card from a DB row.")
    parser.add_argument("--id", required=True, help="recipe id (slug) in recipes.db")
    parser.add_argument(
        "--style", default="b", choices=["b", "c"], help="card template style"
    )
    args = parser.parse_args()

    out_path = render_from_db(args.id, args.style)
    logger.info("card saved as artifact: %s", out_path)


if __name__ == "__main__":
    main()
