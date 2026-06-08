# pyright: reportMissingImports=false
"""Read-only API for browsing the scraped-recipe database.

Surfaces rows from ``recipes.db`` (built by ``recipe-publisher/recipe_db``) so
the web UI can browse scraped recipes, their ratings, and dog-safety verdicts.
Strictly read-only: the SQLite file is opened in ``mode=ro`` and never mutated.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from api.recipe_schemas import (
    PublishChannel,
    RecipeDetail,
    RecipeIngredient,
    RecipesResponse,
    RecipeSummary,
    SyncResponse,
)
from lib.config import settings

# ``recipe_db`` lives in the hyphenated ``recipe-publisher`` dir, which is not
# an importable package name — add it to sys.path so we can reuse the existing
# repository layer instead of re-implementing row deserialization here.
_RECIPE_PUBLISHER = Path(__file__).resolve().parent.parent / "recipe-publisher"
if str(_RECIPE_PUBLISHER) not in sys.path:
    sys.path.insert(0, str(_RECIPE_PUBLISHER))

from recipe_db import db, publish_sync
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository

router = APIRouter(tags=["recipes"])


def _db_path() -> Path:
    """Resolve recipes.db under the brand data dir (500 if BRAND_DIR unbound)."""
    if settings.paths is None:
        raise HTTPException(
            status_code=500,
            detail="settings.paths is unset; BRAND_DIR not resolved",
        )
    return settings.paths.data_dir / "recipes.db"


def _open_readonly() -> sqlite3.Connection:
    """Open recipes.db read-only so browsing never locks an active scrape."""
    path = _db_path()
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"recipe DB not found at {path}"
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _abs_artifacts(rel_path: str) -> str:
    """Resolve a BRAND_DIR-relative artifact path to an absolute path."""
    if not rel_path or settings.paths is None:
        return rel_path
    return str(settings.paths.brand_dir / rel_path)


def _publish_date(row: RecipeRow) -> str:
    """Newest publish date (ISO) across channels, or '' when unpublished."""
    dates = [c.get("at", "") for c in row.publish_status.values()]
    return max(dates) if dates else ""


def _to_summary(row: RecipeRow) -> RecipeSummary:
    return RecipeSummary(
        id=row.id,
        name=row.name,
        display_name=row.display_name,
        artifacts_path=_abs_artifacts(row.artifacts_path),
        wp_url=row.wp_url,
        ig_url=row.ig_url,
        fb_url=row.fb_url,
        published_at=_publish_date(row),
        category=row.category,
        dog_safe=row.dog_safe,
        toxic_flags=row.toxic_flags,
        status=row.status,
        source_url=row.source_url,
        source_name=row.source_name,
        prep_minutes=row.prep_minutes,
        cook_minutes=row.cook_minutes,
        total_minutes=row.total_minutes,
        servings=row.servings,
        publish_status={
            channel: PublishChannel(**fields)
            for channel, fields in row.publish_status.items()
        },
    )


@router.get("/recipes", response_model=RecipesResponse)
def list_recipes(
    status: str | None = Query(None, description="filter by pipeline status"),
    dog_safe: bool | None = Query(None, description="filter by safety verdict"),
) -> RecipesResponse:
    """List stored recipes (alphabetical) with optional filters."""
    conn = _open_readonly()
    try:
        rows = RecipeRepository(conn).list_recipes(status=status)
    finally:
        conn.close()
    if dog_safe is not None:
        rows = [r for r in rows if r.dog_safe == dog_safe]
    # Newest published first; unpublished (no date) fall to the bottom, by name.
    rows.sort(key=lambda r: r.name.lower())
    rows.sort(key=_publish_date, reverse=True)
    return RecipesResponse(
        recipes=[_to_summary(r) for r in rows], total=len(rows)
    )


@router.get("/recipes/{recipe_id}", response_model=RecipeDetail)
def get_recipe(recipe_id: str) -> RecipeDetail:
    """Full detail for one recipe, including ingredients and steps."""
    conn = _open_readonly()
    try:
        row = RecipeRepository(conn).get_recipe(recipe_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"no recipe with id '{recipe_id}'"
        )
    return RecipeDetail(
        **_to_summary(row).model_dump(),
        ingredients=[
            RecipeIngredient(
                item=ing.item, qty=ing.qty, unit=ing.unit, notes=ing.notes
            )
            for ing in row.ingredients
        ],
        steps=row.steps,
        nutrition=row.nutrition,
        tags=row.tags,
        hero_image_url=row.hero_image_url,
    )


@router.post("/recipes/sync-publish", response_model=SyncResponse)
def sync_publish() -> SyncResponse:
    """Refresh each recipe's publish status from the publish records.

    Reads the brand's ``campaigns/**/metadata.json`` and
    ``published_recipes.json`` and writes per-channel status onto matching
    recipe rows. Idempotent and safe to call repeatedly.
    """
    path = _db_path()
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"recipe DB not found at {path}"
        )
    campaigns_root = settings.paths.campaigns_dir if settings.paths else None
    published_recipes = _RECIPE_PUBLISHER / "state" / "published_recipes.json"
    conn = db.connect(path)
    try:
        db.migrate(conn)
        repo = RecipeRepository(conn)
        updated = publish_sync.sync_publish_status(
            repo, campaigns_root, published_recipes
        )
        total = len(repo.list_recipes())
    finally:
        conn.close()
    return SyncResponse(updated=updated, total=total)
