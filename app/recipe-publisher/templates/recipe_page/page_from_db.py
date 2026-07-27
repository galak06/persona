# pyright: reportMissingImports=false, reportMissingModuleSource=false
# (the PostToolUse hook type-checks a /tmp copy where sibling modules + the
#  project venv aren't on the path; resolve those diagnostics inline.)
"""Map a ``recipes.db`` row into ``RecipePageData`` for the page renderer.

Shared by the CLI (writes a relative-path artifact file) and the read-only API
(serves the page with artifact-URL image refs) so the row→page mapping and the
quantity humanizing live in exactly one place.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from page_render import RecipePageData
from recipe_db.models import Ingredient, RecipeRow

# Decimals the scraper stores (e.g. 0.33333334326744 cup) → typeset fractions.
_DECIMAL_FRACTIONS: list[tuple[float, str]] = [
    (0.125, "⅛"), (0.25, "¼"), (0.333, "⅓"), (0.375, "⅜"), (0.5, "½"),
    (0.625, "⅝"), (0.667, "⅔"), (0.75, "¾"), (0.875, "⅞"),
]
_LEADING_NUMBER = re.compile(r"^(\d+(?:\.\d+)?)(\s+.*)?$")

# A reference builder: maps an artifact-folder-relative path (e.g.
# "images/featured.jpg") to whatever the consumer embeds in <img src> — a
# relative path for a saved file, or an absolute artifact URL for the API.
RefBuilder = Callable[[str], str]


def humanize_qty(text: str) -> str:
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


def ingredient_lines(ingredients: list[Ingredient]) -> list[str]:
    """Turn stored ingredients into clean display lines with fraction glyphs."""
    lines: list[str] = []
    for ing in ingredients:
        text = (ing.item or " ".join(p for p in (ing.qty, ing.unit, ing.notes) if p)).strip()
        if text:
            lines.append(humanize_qty(text))
    return lines


def _hero_ref(row: RecipeRow, images_dir: Path, ref: RefBuilder) -> str:
    """Local featured.jpg if present, else the remote source URL, else empty."""
    if (images_dir / "featured.jpg").exists():
        return ref("images/featured.jpg")
    return row.hero_image_url or ""


def _gallery_refs(images_dir: Path, ref: RefBuilder) -> list[str]:
    """Refs to the rendered card + any carousel slides that exist on disk."""
    refs: list[str] = []
    if (images_dir / "recipe_card.png").exists():
        refs.append(ref("images/recipe_card.png"))
    slides_dir = images_dir / "slides"
    if slides_dir.is_dir():
        refs += [ref(f"images/slides/{p.name}") for p in sorted(slides_dir.glob("slide_*.jpg"))]
    return refs


def page_data_from_row(
    row: RecipeRow,
    images_dir: Path,
    ref: RefBuilder,
    *,
    associates_tag: str = "",
) -> RecipePageData:
    """Build renderer input from a DB row + its on-disk image artifacts."""
    return RecipePageData(
        title=row.display_name or row.name or row.id,
        ingredients=ingredient_lines(row.ingredients),
        steps=list(row.steps),
        prep_minutes=row.prep_minutes or None,
        cook_minutes=row.cook_minutes or None,
        total_minutes=row.total_minutes or None,
        servings=row.servings,
        category=row.category,
        tags=list(row.tags),
        meta_description=(row.generated_content or {}).get("meta_description", ""),
        hero_ref=_hero_ref(row, images_dir, ref),
        gallery_refs=_gallery_refs(images_dir, ref),
        affiliate_products=list(row.affiliate_products),
        associates_tag=associates_tag,
        source_name=row.source_name,
        source_url=row.source_url,
    )
