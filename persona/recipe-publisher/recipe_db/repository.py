# pyright: reportMissingImports=false
"""Data-access layer for raw scrapes and normalized recipes (Supabase backend)."""

from __future__ import annotations

import logging
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from recipe_db.models import ContentStatus, Ingredient, RecipeRow

_SA_ROOT = Path(__file__).resolve().parents[2]
if str(_SA_ROOT) not in sys.path:
    sys.path.insert(0, str(_SA_ROOT))

from lib.supabase_client import get_client

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _j(val: Any, default: Any) -> Any:
    """Return val as-is if already a Python object; parse JSON string if needed."""
    if val is None:
        return default
    if isinstance(val, str):
        import json
        return json.loads(val) if val else default
    return val


def _channel_urls(
    publish_status: dict[str, dict[str, str]],
) -> tuple[str, str, str]:
    return (
        publish_status.get("wp", {}).get("url", ""),
        publish_status.get("ig", {}).get("url", ""),
        publish_status.get("fb", {}).get("url", ""),
    )


def _row_to_recipe(row: Any) -> RecipeRow:
    """Deserialize a Supabase row (JSONB cols already parsed) into a RecipeRow."""
    raw_ings = _j(row.get("ingredients"), [])
    ingredients = [Ingredient(**ing) for ing in raw_ings]
    return RecipeRow(
        id=row.get("id", ""),
        name=row.get("name") or row.get("title") or "",
        display_name=row.get("display_name") or "",
        artifacts_path=row.get("artifacts_path") or "",
        card_path=row.get("card_path") or "",
        card_created_at=row.get("card_created_at") or "",
        card_html_created_at=row.get("card_html_created_at") or "",
        wp_url=row.get("wp_url") or "",
        ig_url=row.get("ig_url") or "",
        fb_url=row.get("fb_url") or "",
        ingredients=ingredients,
        steps=_j(row.get("steps"), []),
        prep_minutes=row.get("prep_minutes") or 0,
        cook_minutes=row.get("cook_minutes") or 0,
        total_minutes=row.get("total_minutes") or 0,
        servings=row.get("servings") or "",
        nutrition=_j(row.get("nutrition"), {}),
        category=row.get("category") or "",
        tags=_j(row.get("tags"), []),
        hero_image_url=row.get("hero_image_url") or "",
        source_url=row.get("source_url") or "",
        source_name=row.get("source_name") or "",
        license=row.get("license") or "",
        content_hash=row.get("content_hash") or "",
        publish_status=_j(row.get("publish_status"), {}),
        status=row.get("status") or "",
        toxic_flags=_j(row.get("toxic_flags"), []),
        dog_safe=bool(row.get("dog_safe")),
        override=bool(row.get("override")),
        season_tags=_j(row.get("season_tags"), []),
        affiliate_products=_j(row.get("affiliate_products"), []),
        generated_content=_j(row.get("generated_content"), {}),
        content_status=row.get("content_status") or "none",
        publish_results=_j(row.get("publish_results"), []),
        wp_post_id=row.get("wp_post_id"),
        pdf_url=row.get("pdf_url") or "",
        content_created_at=row.get("content_created_at") or "",
        image_created_at=row.get("image_created_at") or "",
        slides_created_at=row.get("slides_created_at") or "",
        slides_count=row.get("slides_count") or 0,
        reel_created_at=row.get("reel_created_at") or "",
        audio_ready_at=row.get("audio_ready_at") or "",
        wp_audio_updated_at=row.get("wp_audio_updated_at") or "",
        social_published_at=row.get("social_published_at") or "",
    )


def _rows(data: Any) -> list[Any]:
    """Cast supabase result.data (JSON alias) to a plain list for safe iteration."""
    return cast(list[Any], data)


class RecipeRepository:
    """CRUD + dedup operations over the recipe tables (Supabase backend)."""

    def __init__(self, conn: Any = None) -> None:
        # conn param kept for backward compat; Supabase client used directly
        pass

    def _update(self, recipe_id: str, **fields: Any) -> None:
        payload: Any = {"updated_at": _now(), **fields}
        get_client().table("recipes").update(payload).eq("id", recipe_id).execute()

    # ------------------------------------------------------------------ raw
    def insert_raw(
        self,
        source_url: str,
        source_name: str,
        payload: dict[str, object],
        content_hash: str,
        scraped_at: str,
    ) -> bool:
        """Insert an immutable raw scrape. Returns False on duplicate hash."""
        raw: Any = {
            "source_url": source_url,
            "source_name": source_name,
            "scraped_at": scraped_at,
            "content_hash": content_hash,
            "payload": payload,
        }
        result = (
            get_client()
            .table("raw_scrapes")
            .upsert(raw, on_conflict="content_hash", ignore_duplicates=True)
            .execute()
        )
        inserted = len(result.data) > 0
        if not inserted:
            logger.debug("raw scrape duplicate skipped: %s", content_hash)
        return inserted

    # -------------------------------------------------------------- recipes
    def upsert_recipe(self, row: RecipeRow) -> None:
        """Insert or replace a recipe. created_at is preserved on conflict."""
        recipe_id = row.ensure_id()
        wp_url, ig_url, fb_url = _channel_urls(row.publish_status)
        data: Any = {
            "id": recipe_id,
            "title": row.name,
            "name": row.name,
            "display_name": row.display_name,
            "artifacts_path": row.artifacts_path,
            "wp_url": wp_url,
            "ig_url": ig_url,
            "fb_url": fb_url,
            "category": row.category,
            "prep_minutes": row.prep_minutes,
            "cook_minutes": row.cook_minutes,
            "total_minutes": row.total_minutes,
            "servings": row.servings,
            "ingredients": [asdict(ing) for ing in row.ingredients],
            "steps": row.steps,
            "nutrition": row.nutrition,
            "tags": row.tags,
            "hero_image_url": row.hero_image_url,
            "source_url": row.source_url,
            "source_name": row.source_name,
            "license": row.license,
            "content_hash": row.content_hash,
            "status": row.status,
            "toxic_flags": row.toxic_flags,
            "dog_safe": row.dog_safe,
            "override": row.override,
            "publish_status": row.publish_status,
            "season_tags": row.season_tags,
            "affiliate_products": row.affiliate_products,
            "generated_content": row.generated_content,
            "content_status": row.content_status,
            "publish_results": row.publish_results,
            "updated_at": _now(),
        }
        get_client().table("recipes").upsert(data).execute()

    def get_recipe(self, recipe_id: str) -> RecipeRow | None:
        result = get_client().table("recipes").select("*").eq("id", recipe_id).execute()
        rows = _rows(result.data)
        return _row_to_recipe(rows[0]) if rows else None

    def list_recipes(self, status: str | None = None, limit: int = 0) -> list[RecipeRow]:
        q = get_client().table("recipes").select("*").order("id")
        if status is not None:
            q = q.eq("status", status)
        if limit > 0:
            q = q.limit(limit)
        return [_row_to_recipe(r) for r in _rows(q.execute().data)]

    # ----------------------------------------------------------------- dedup
    def exists_by_content_hash(self, content_hash: str) -> bool:
        result = get_client().table("recipes").select("id").eq("content_hash", content_hash).limit(1).execute()
        return len(result.data) > 0

    def exists_by_id(self, recipe_id: str) -> bool:
        result = get_client().table("recipes").select("id").eq("id", recipe_id).limit(1).execute()
        return len(result.data) > 0

    # ---------------------------------------------------------------- status
    def set_status(self, recipe_id: str, status: str) -> None:
        self._update(recipe_id, status=status)

    def set_safety(self, recipe_id: str, toxic_flags: list[str], dog_safe: bool) -> None:
        self._update(recipe_id, toxic_flags=toxic_flags, dog_safe=dog_safe)

    def set_publish_status(
        self, recipe_id: str, publish_status: dict[str, dict[str, str]]
    ) -> None:
        wp_url, ig_url, fb_url = _channel_urls(publish_status)
        self._update(
            recipe_id,
            publish_status=publish_status,
            wp_url=wp_url,
            ig_url=ig_url,
            fb_url=fb_url,
        )

    def set_season_tags(self, recipe_id: str, season_tags: list[str]) -> None:
        self._update(recipe_id, season_tags=season_tags)

    def set_affiliate_products(self, recipe_id: str, products: list[dict[str, str]]) -> None:
        self._update(recipe_id, affiliate_products=products)

    def set_generated_content(
        self, recipe_id: str, content: dict[str, str], content_status: str
    ) -> None:
        self._update(recipe_id, generated_content=content, content_status=content_status)

    def set_content_status(self, recipe_id: str, content_status: str) -> None:
        self._update(recipe_id, content_status=content_status)

    def set_publish_results(self, recipe_id: str, results: list[dict[str, str]]) -> None:
        self._update(recipe_id, publish_results=results)

    def set_display_name(self, recipe_id: str, display_name: str) -> None:
        self._update(recipe_id, display_name=display_name)

    def set_artifacts_path(self, recipe_id: str, artifacts_path: str) -> None:
        self._update(recipe_id, artifacts_path=artifacts_path)

    def set_html_exported_at(self, recipe_id: str, ts: str) -> None:
        self._update(recipe_id, html_exported_at=ts)

    def set_card(self, recipe_id: str, card_path: str) -> None:
        self._update(recipe_id, card_path=card_path, card_created_at=_now())

    def set_card_html(self, recipe_id: str, html_path: str) -> None:
        self._update(recipe_id, card_html_path=html_path, card_html_created_at=_now())

    def set_wp_post_id(self, recipe_id: str, wp_post_id: int) -> None:
        self._update(recipe_id, wp_post_id=wp_post_id)

    def set_pdf_url(self, recipe_id: str, pdf_url: str) -> None:
        self._update(recipe_id, pdf_url=pdf_url)

    def set_slides(self, recipe_id: str, slides_count: int, ts: str) -> None:
        self._update(recipe_id, slides_count=slides_count, slides_created_at=ts)

    def set_image_created_at(self, recipe_id: str, ts: str) -> None:
        self._update(recipe_id, image_created_at=ts)

    def set_reel(self, recipe_id: str, ts: str) -> None:
        self._update(recipe_id, reel_created_at=ts)

    def set_content(self, recipe_id: str, ts: str) -> None:
        self._update(recipe_id, content_created_at=ts)

    def set_audio_ready(self, recipe_id: str, ts: str) -> None:
        self._update(recipe_id, audio_ready_at=ts)

    def set_wp_audio_updated(self, recipe_id: str, ts: str) -> None:
        self._update(recipe_id, wp_audio_updated_at=ts)

    def set_social_published(self, recipe_id: str, ts: str) -> None:
        self._update(recipe_id, social_published_at=ts)

    # --------------------------------------------------------------- queries
    def list_by_content_status(self, content_status: str) -> list[RecipeRow]:
        result = (
            get_client().table("recipes").select("*").eq("content_status", content_status).order("id").execute()
        )
        return [_row_to_recipe(r) for r in _rows(result.data)]

    def list_published_ids(self) -> set[str]:
        result = get_client().table("recipes").select("id,content_status,wp_url").execute()
        rows = _rows(result.data)
        return {
            str(r["id"]) for r in rows
            if r.get("content_status") == ContentStatus.PUBLISHED or r.get("wp_url", "")
        }
