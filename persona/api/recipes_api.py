# pyright: reportMissingImports=false
"""Read-only API for browsing the scraped-recipe database.

Surfaces rows from ``recipes.db`` (built by ``recipe-publisher/recipe_db``) so
the web UI can browse scraped recipes, their ratings, and dog-safety verdicts.
Strictly read-only: the SQLite file is opened in ``mode=ro`` and never mutated.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response

from api.recipe_schemas import (
    AffiliateProduct,
    AnalyticsResponse,
    ArtifactItem,
    ArtifactsResponse,
    PublishChannel,
    RecipeDetail,
    RecipeIngredient,
    RecipeMedia,
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
_RECIPE_PAGE_DIR = _RECIPE_PUBLISHER / "templates" / "recipe_page"
for _p in (_RECIPE_PUBLISHER, _RECIPE_PAGE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from page_from_db import page_data_from_row
from page_render import build_page_html
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
    return settings.paths.data_dir / "db" / "recipes.db"


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
    rel = (row.artifacts_path if row else "") or f"data/media/recipe_artifacts/{recipe_id}"
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


def _media_from_row(row: RecipeRow) -> RecipeMedia | None:
    """Parse the row's ``generated_content.media`` manifest into RecipeMedia.

    The manifest is stored as a JSON *string* (so ``generated_content`` keeps
    its ``dict[str, str]`` shape). Paths are BRAND_DIR-relative; the UI turns
    them into URLs via the ``/recipes/{id}/media-file`` endpoint. Returns None
    when no media has been catalogued for the recipe.
    """
    raw = row.generated_content.get("media")
    if not raw:
        return None
    try:
        manifest = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return RecipeMedia(
        images=list(manifest.get("images", [])),
        reels=list(manifest.get("reels", [])),
        audio=list(manifest.get("audio", [])),
        featured_image=manifest.get("featured_image"),
    )


def _to_summary(row: RecipeRow) -> RecipeSummary:
    return RecipeSummary(
        id=row.id,
        name=row.name,
        display_name=row.display_name,
        artifacts_path=_abs_artifacts(row.artifacts_path),
        card_path=_abs_artifacts(row.card_path),
        card_created_at=row.card_created_at,
        card_html_path=_abs_artifacts(row.card_html_path),
        card_html_created_at=row.card_html_created_at,
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
        media=_media_from_row(row),
    )


@router.get("/recipes/{recipe_id}/page", response_class=HTMLResponse)
def recipe_page(recipe_id: str) -> HTMLResponse:
    """Render the recipe as a standalone HTML page (DB fields + image artifacts).

    Image refs point at the artifact endpoint so photos resolve inside an
    ``<iframe>`` preview. Re-rendered per call to reflect current DB state — the
    same markup the publisher emits, viewable before anything goes live.
    """
    conn = _open_readonly()
    try:
        row = RecipeRepository(conn).get_recipe(recipe_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no recipe with id '{recipe_id}'")

    def _artifact_ref(rel: str) -> str:
        return f"/api/v1/recipes/{quote(recipe_id)}/artifact?path={quote(rel)}"

    data = page_data_from_row(
        row,
        _artifacts_dir(recipe_id) / "images",
        _artifact_ref,
        associates_tag=os.environ.get("AMAZON_ASSOCIATES_TAG", "").strip(),
    )
    return HTMLResponse(build_page_html(data))


@router.get("/recipes/{recipe_id}/image-preview", response_class=HTMLResponse)
def recipe_image_preview(recipe_id: str) -> HTMLResponse:
    """Render the post_image.html recipe card with base64-embedded assets.

    The card shows: food photo | dog avatar | recipe name | ingredients | timing.
    All assets are inlined as base64 data URIs so the page is fully self-contained.
    Returns a green placeholder when ``post_image.jpg`` has not yet been generated.
    """
    import base64
    from html import escape

    if settings.paths is None:
        raise HTTPException(
            status_code=500, detail="settings.paths unset; BRAND_DIR not resolved"
        )

    conn = _open_readonly()
    try:
        row = RecipeRepository(conn).get_recipe(recipe_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no recipe with id '{recipe_id}'")

    template_path = _RECIPE_PUBLISHER / "templates" / "post_image.html"
    if not template_path.exists():
        raise HTTPException(
            status_code=500, detail=f"post_image.html not found at {template_path}"
        )
    html = template_path.read_text(encoding="utf-8")

    # --- Food photo (left panel) ---
    img_path = settings.paths.campaigns_dir / "recipes" / "ready" / recipe_id / "post_image.jpg"
    if img_path.exists():
        img_b64 = base64.b64encode(img_path.read_bytes()).decode()
        food_photo_html = f'<img class="food-photo" src="data:image/jpeg;base64,{img_b64}" alt="recipe">'
    else:
        food_photo_html = '<div class="photo-placeholder">🐾</div>'

    # --- Dog avatar (brand logo) ---
    brand_dir = settings.paths.brand_dir
    avatar_src = ""
    for candidate in [
        brand_dir / "images" / "badge.png",
        brand_dir / "images" / "badge.jpg",
        brand_dir / "data" / "assets" / "badge.png",
        brand_dir / "data" / "assets" / "nalla-avatar.png",
        brand_dir / "data" / "assets" / "logo.png",
    ]:
        if candidate.exists():
            ext = candidate.suffix.lstrip(".")
            mime = "png" if ext == "png" else "jpeg"
            b64 = base64.b64encode(candidate.read_bytes()).decode()
            avatar_src = f"data:image/{mime};base64,{b64}"
            break

    if avatar_src:
        dog_avatar_html = f'<img class="brand-avatar" src="{avatar_src}" alt="Nalla">'
    else:
        dog_avatar_html = '<div class="avatar-placeholder">🐶</div>'

    # --- Ingredients ---
    MAX_INGREDIENTS = 12
    items = (row.ingredients or [])[:MAX_INGREDIENTS]
    ing_lines = []
    for ing in items:
        qty_unit = " ".join(filter(None, [ing.qty, ing.unit]))
        qty_html = f'<span class="ing-qty">{escape(qty_unit)}</span> ' if qty_unit else ""
        item_text = escape(ing.item)
        if ing.notes:
            item_text += f', <em style="color:#888;font-size:13px">{escape(ing.notes)}</em>'
        ing_lines.append(
            f'<li class="ing-item"><span class="ing-dot"></span>'
            f'<span>{qty_html}{item_text}</span></li>'
        )
    ingredients_html = "\n        ".join(ing_lines)

    # --- Meta bar (prep / cook / servings) ---
    def _fmt_time(minutes: int) -> str:
        if not minutes:
            return "—"
        return f"{minutes} min" if minutes < 60 else f"{minutes // 60}h {minutes % 60}m".replace(" 0m", "")

    meta_items = [
        ("Prep", _fmt_time(row.prep_minutes)),
        ("Cook", _fmt_time(row.cook_minutes)),
    ]
    if row.servings:
        meta_items.append(("Serves", escape(str(row.servings))))

    meta_html = "\n        ".join(
        f'<div class="meta-item">'
        f'<span class="meta-label">{label}</span>'
        f'<span class="meta-value">{value}</span>'
        f'</div>'
        for label, value in meta_items
    )

    recipe_name = escape(row.display_name or row.name or recipe_id)

    html = html.replace("{{FOOD_PHOTO_HTML}}", food_photo_html)
    html = html.replace("{{DOG_AVATAR_HTML}}", dog_avatar_html)
    html = html.replace("{{RECIPE_NAME}}", recipe_name)
    html = html.replace("{{INGREDIENTS_HTML}}", ingredients_html)
    html = html.replace("{{META_HTML}}", meta_html)

    return HTMLResponse(html)


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


@router.get("/recipes/{recipe_id}/media-file")
def get_media_file(
    recipe_id: str,
    path: str = Query(..., description="BRAND_DIR-relative media path from the manifest"),
) -> FileResponse:
    """Serve a media file referenced by a recipe's media manifest.

    Paths are BRAND_DIR-relative and may live under either the live
    ``recipe_artifacts`` folder or the ``_migrated_backup`` folder, so this
    guards to *both* of the recipe's own media dirs (path-traversal safe) rather
    than the single artifacts dir used by ``get_artifact``.
    """
    if settings.paths is None:
        raise HTTPException(
            status_code=500, detail="settings.paths unset; BRAND_DIR not resolved"
        )
    brand_dir = settings.paths.brand_dir.resolve()
    target = (brand_dir / path).resolve()
    allowed = (
        (brand_dir / "data" / "media" / "recipe_artifacts" / recipe_id).resolve(),
        (brand_dir / "data" / "media" / "_migrated_backup" / recipe_id).resolve(),
    )
    if not any(target.is_relative_to(folder) for folder in allowed):
        raise HTTPException(status_code=400, detail="invalid media path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="media file not found")
    return FileResponse(target)


@router.get("/recipes/{recipe_id}/story-card")
def get_story_card(recipe_id: str) -> Response:
    """Generate and return a story card JPEG for the given recipe."""
    conn = _open_readonly()
    try:
        row = RecipeRepository(conn).get_recipe(recipe_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no recipe with id '{recipe_id}'")

    from workers.worker_post_stories import _resolve_image_url
    import httpx as _httpx

    image_url = _resolve_image_url(row)
    if not image_url:
        raise HTTPException(status_code=422, detail="no image available for this recipe")

    img_resp = _httpx.get(image_url, follow_redirects=True, timeout=30.0)
    img_resp.raise_for_status()

    from generators.story_card import compose_story_card

    recipe_name = row.display_name or getattr(row, "name", None) or row.id
    _BADGE_PATH = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "../../persona/images/badge.png",
    ))
    card_bytes = compose_story_card(
        img_resp.content,
        recipe_name,
        row.wp_url or "",
        badge_path=_BADGE_PATH if os.path.exists(_BADGE_PATH) else "",
    )

    return Response(content=card_bytes, media_type="image/jpeg")
