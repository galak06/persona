# pyright: reportMissingImports=false
"""Pipeline helper: render recipe_page.html into the recipe's artifacts dir.

Triggers when a recipe is WP-published AND a PDF artifact already exists on
disk (meaning the card/carousel render phases have completed). Writes
``recipe_page.html`` atomically and records ``html_exported_at`` in the DB.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure the recipe_page template helpers are importable — they live in a
# non-package directory, so we add it explicitly (same pattern as
# scripts/render_page_from_db.py).
_RECIPE_PUBLISHER = Path(__file__).resolve().parent.parent
_RECIPE_PAGE_DIR = _RECIPE_PUBLISHER / "templates" / "recipe_page"
for _p in (_RECIPE_PUBLISHER, _RECIPE_PAGE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from page_from_db import page_data_from_row  # noqa: E402
from page_render import build_page_html  # noqa: E402
from recipe_db.models import RecipeRow  # noqa: E402


def can_export(recipe: RecipeRow, brand_dir: Path) -> bool:
    """True when WP is published AND a PDF artifact exists on disk."""
    wp_ok = recipe.publish_status.get("wp", {}).get("state") == "published"
    if not recipe.artifacts_path:
        return False
    artifacts = brand_dir / recipe.artifacts_path
    pdf_ok = artifacts.is_dir() and any(artifacts.glob("*.pdf"))
    return wp_ok and pdf_ok


def export_html(
    recipe: RecipeRow,
    brand_dir: Path,
    repo,  # RecipeRepository — typed loosely to avoid circular import
    *,
    associates_tag: str = "",
) -> Path:
    """Render recipe_page.html into artifacts dir; update DB html_exported_at.

    Returns the Path to the written file.
    """
    # Resolve (and create if needed) the artifacts directory.
    artifacts_dir = brand_dir / recipe.artifacts_path
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Images are expected alongside the card/carousel output under images/.
    images_dir = artifacts_dir / "images"

    # ref=identity keeps <img src> as artifact-relative paths so the saved
    # file opens correctly straight off disk (same as render_page_from_db.py).
    data = page_data_from_row(
        recipe,
        images_dir,
        ref=lambda rel: rel,
        associates_tag=associates_tag,
    )
    html = build_page_html(data)

    # Atomic write: write to .tmp then rename to avoid partial reads.
    out_path = artifacts_dir / "recipe_page.html"
    tmp_path = out_path.with_suffix(".html.tmp")
    tmp_path.write_text(html, encoding="utf-8")
    tmp_path.replace(out_path)

    ts = datetime.now(timezone.utc).isoformat()
    repo.set_html_exported_at(recipe.id, ts)

    return out_path
