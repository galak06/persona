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
from fastapi.responses import FileResponse

from api.recipe_schemas import (
    AffiliateProduct,
    AnalyticsResponse,
    ArtifactItem,
    ArtifactsResponse,
    PublishChannel,
    RecipeDetail,
    RecipeIngredient,
    RecipesResponse,
    RecipeSummary,
    StatusChangeResponse,
    SyncResponse,
)
from lib.config import settings

# ``recipe_db`` lives in the hyphenated ``recipe-publisher`` dir, which is not
# an importable package name — add it to sys.path so we can reuse the existing
# repository layer instead of re-implementing row deserialization here.
_RECIPE_PUBLISHER = Path(__file__).resolve().parent.parent / "recipe-publisher"
if str(_RECIPE_PUBLISHER) not in sys.path:
    sys.path.insert(0, str(_RECIPE_PUBLISHER))

from pipeline import seasons
from pipeline.analytics import AnalyticsTracker
from pipeline.approval import ApprovalError, ApprovalService
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


def _artifacts_dir(recipe_id: str) -> Path:
    """Resolve a recipe's artifact folder (row ``artifacts_path``, or convention)."""
    if settings.paths is None:
        raise HTTPException(
            status_code=500, detail="settings.paths unset; BRAND_DIR not resolved"
        )
    conn = _open_readonly()
    try:
        row = RecipeRepository(conn).get_recipe(recipe_id)
    finally:
        conn.close()
    rel = (row.artifacts_path if row else "") or f"data/recipe_artifacts/{recipe_id}"
    return settings.paths.brand_dir / rel


def _artifact_kind(name: str) -> str:
    """Classify an artifact file by extension for the UI."""
    suffix = Path(name).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".json":
        return "json"
    return "other"


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
        card_path=_abs_artifacts(row.card_path),
        card_created_at=row.card_created_at,
        wp_url=row.wp_url,
        ig_url=row.ig_url,
        fb_url=row.fb_url,
        published_at=_publish_date(row),
        category=row.category,
        dog_safe=row.dog_safe,
        toxic_flags=row.toxic_flags,
        season_tags=row.season_tags,
        affiliate_products=[
            AffiliateProduct(**p) for p in row.affiliate_products
        ],
        content_status=row.content_status,
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
    season: str | None = Query(
        None, description="filter to recipes in-season for this season"
    ),
    content_status: str | None = Query(
        None, description="filter by publish-content lifecycle state"
    ),
) -> RecipesResponse:
    """List stored recipes (alphabetical) with optional filters."""
    conn = _open_readonly()
    try:
        rows = RecipeRepository(conn).list_recipes(status=status)
    finally:
        conn.close()
    if dog_safe is not None:
        rows = [r for r in rows if r.dog_safe == dog_safe]
    if content_status is not None:
        rows = [r for r in rows if r.content_status == content_status]
    if season is not None:
        try:
            target = seasons.normalize_season(season)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Prefer stored season_tags; fall back to on-the-fly inference so the
        # filter works even before the seasonal-selection phase has persisted.
        rows = [
            r
            for r in rows
            if seasons.in_season(
                r.season_tags
                or seasons.infer_seasons(r.name, r.tags, r.category),
                target,
            )
        ]
    # Newest published first; unpublished (no date) fall to the bottom, by name.
    rows.sort(key=lambda r: r.name.lower())
    rows.sort(key=_publish_date, reverse=True)
    return RecipesResponse(
        recipes=[_to_summary(r) for r in rows], total=len(rows)
    )


def _open_writable() -> sqlite3.Connection:
    """Open recipes.db read-write (for approval transitions)."""
    path = _db_path()
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"recipe DB not found at {path}")
    conn = db.connect(path)
    db.migrate(conn)
    return conn


@router.get("/recipes/analytics", response_model=AnalyticsResponse)
def recipes_analytics() -> AnalyticsResponse:
    """Aggregated publish outcomes across all recipes (phase 10, local log)."""
    conn = _open_readonly()
    try:
        report = AnalyticsTracker(RecipeRepository(conn)).run()
    finally:
        conn.close()
    return AnalyticsResponse(
        recipes=report.recipes,
        attempts=report.attempts,
        by_platform=report.by_platform,
        by_status=report.by_status,
    )


@router.post("/recipes/{recipe_id}/approve", response_model=StatusChangeResponse)
def approve_recipe(recipe_id: str) -> StatusChangeResponse:
    """Promote a pending recipe to approved (the human gate, phase 5)."""
    conn = _open_writable()
    try:
        ApprovalService(RecipeRepository(conn)).approve(recipe_id)
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
    return StatusChangeResponse(id=recipe_id, content_status="approved")


@router.post("/recipes/{recipe_id}/reject", response_model=StatusChangeResponse)
def reject_recipe(recipe_id: str) -> StatusChangeResponse:
    """Reject a pending recipe so it is never published (phase 5)."""
    conn = _open_writable()
    try:
        ApprovalService(RecipeRepository(conn)).reject(recipe_id)
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
    return StatusChangeResponse(id=recipe_id, content_status="rejected")


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


@router.get("/recipes/{recipe_id}/artifacts", response_model=ArtifactsResponse)
def list_artifacts(recipe_id: str) -> ArtifactsResponse:
    """List every file in the recipe's artifact folder, for the UI viewer."""
    base = _artifacts_dir(recipe_id)
    items: list[ArtifactItem] = []
    if base.is_dir():
        for f in sorted(base.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                items.append(
                    ArtifactItem(
                        name=f.name,
                        path=f.relative_to(base).as_posix(),
                        kind=_artifact_kind(f.name),
                        size=f.stat().st_size,
                    )
                )
    return ArtifactsResponse(
        recipe_id=recipe_id, artifacts=items, total=len(items)
    )


@router.get("/recipes/{recipe_id}/artifact")
def get_artifact(
    recipe_id: str,
    path: str = Query(..., description="path relative to the artifact folder"),
) -> FileResponse:
    """Serve a single artifact file (path-traversal guarded)."""
    base = _artifacts_dir(recipe_id).resolve()
    target = (base / path).resolve()
    if not target.is_relative_to(base):
        raise HTTPException(status_code=400, detail="invalid artifact path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(target)
