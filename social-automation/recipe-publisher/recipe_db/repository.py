# pyright: reportMissingImports=false
"""Data-access layer for raw scrapes and normalized recipes.

`RecipeRepository` wraps a single sqlite3 connection and owns all (de)serialization
of JSON columns. Dedup is enforced on two keys: exact `content_hash` and the
normalized-title slug (`recipes.id`).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict

from recipe_db.models import Ingredient, RecipeRow

logger = logging.getLogger(__name__)


def _channel_urls(
    publish_status: dict[str, dict[str, str]],
) -> tuple[str, str, str]:
    """Extract flat (wp_url, ig_url, fb_url) from a publish_status dict."""
    return (
        publish_status.get("wp", {}).get("url", ""),
        publish_status.get("ig", {}).get("url", ""),
        publish_status.get("fb", {}).get("url", ""),
    )


class RecipeRepository:
    """CRUD + dedup operations over the recipe tables."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

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
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO raw_scrapes
                (source_url, source_name, scraped_at, content_hash, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                source_url,
                source_name,
                scraped_at,
                content_hash,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        self._conn.commit()
        inserted = cur.rowcount > 0
        if not inserted:
            logger.debug("raw scrape duplicate skipped: %s", content_hash)
        return inserted

    # -------------------------------------------------------------- recipes
    def upsert_recipe(self, row: RecipeRow) -> None:
        """Insert or replace a recipe, serializing list/dict fields to JSON.

        `created_at` is preserved on update via COALESCE; `updated_at` is set to
        SQL CURRENT_TIMESTAMP. `id` is derived from the name slug if unset.
        """
        recipe_id = row.ensure_id()
        ingredients_json = json.dumps(
            [asdict(ing) for ing in row.ingredients], ensure_ascii=False
        )
        wp_url, ig_url, fb_url = _channel_urls(row.publish_status)
        self._conn.execute(
            """
            INSERT INTO recipes (
                id, title, name, display_name, artifacts_path,
                wp_url, ig_url, fb_url, category,
                prep_minutes, cook_minutes, total_minutes, servings,
                ingredients, steps, nutrition, tags, hero_image_url, source_url,
                source_name, license, content_hash, status, toxic_flags,
                dog_safe, override, publish_status, season_tags,
                affiliate_products, generated_content, content_status,
                publish_results, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT(id) DO UPDATE SET
                title          = excluded.title,
                name           = excluded.name,
                category       = excluded.category,
                prep_minutes   = excluded.prep_minutes,
                cook_minutes   = excluded.cook_minutes,
                total_minutes  = excluded.total_minutes,
                servings       = excluded.servings,
                ingredients    = excluded.ingredients,
                steps          = excluded.steps,
                nutrition      = excluded.nutrition,
                tags           = excluded.tags,
                hero_image_url = excluded.hero_image_url,
                source_url     = excluded.source_url,
                source_name    = excluded.source_name,
                license        = excluded.license,
                content_hash   = excluded.content_hash,
                status         = excluded.status,
                toxic_flags    = excluded.toxic_flags,
                dog_safe       = excluded.dog_safe,
                override       = excluded.override,
                season_tags    = excluded.season_tags,
                affiliate_products = excluded.affiliate_products,
                generated_content = excluded.generated_content,
                content_status = excluded.content_status,
                publish_results = excluded.publish_results,
                created_at     = COALESCE(recipes.created_at, CURRENT_TIMESTAMP),
                updated_at     = CURRENT_TIMESTAMP
            """,
            (
                recipe_id,
                row.name,
                row.name,
                row.display_name,
                row.artifacts_path,
                wp_url,
                ig_url,
                fb_url,
                row.category,
                row.prep_minutes,
                row.cook_minutes,
                row.total_minutes,
                row.servings,
                ingredients_json,
                json.dumps(row.steps, ensure_ascii=False),
                json.dumps(row.nutrition, ensure_ascii=False),
                json.dumps(row.tags, ensure_ascii=False),
                row.hero_image_url,
                row.source_url,
                row.source_name,
                row.license,
                row.content_hash,
                row.status,
                json.dumps(row.toxic_flags, ensure_ascii=False),
                1 if row.dog_safe else 0,
                1 if row.override else 0,
                json.dumps(row.publish_status, ensure_ascii=False),
                json.dumps(row.season_tags, ensure_ascii=False),
                json.dumps(row.affiliate_products, ensure_ascii=False),
                json.dumps(row.generated_content, ensure_ascii=False),
                row.content_status,
                json.dumps(row.publish_results, ensure_ascii=False),
            ),
        )
        self._conn.commit()

    def get_recipe(self, recipe_id: str) -> RecipeRow | None:
        """Fetch one recipe by slug id, or None."""
        cur = self._conn.execute(
            "SELECT * FROM recipes WHERE id = ?", (recipe_id,)
        )
        sql_row = cur.fetchone()
        return self._row_to_recipe(sql_row) if sql_row is not None else None

    def list_recipes(self, status: str | None = None) -> list[RecipeRow]:
        """List recipes, optionally filtered by status."""
        if status is None:
            cur = self._conn.execute("SELECT * FROM recipes ORDER BY id")
        else:
            cur = self._conn.execute(
                "SELECT * FROM recipes WHERE status = ? ORDER BY id", (status,)
            )
        return [self._row_to_recipe(r) for r in cur.fetchall()]

    # ----------------------------------------------------------------- dedup
    def exists_by_content_hash(self, content_hash: str) -> bool:
        """True if any recipe already has this exact content hash."""
        cur = self._conn.execute(
            "SELECT 1 FROM recipes WHERE content_hash = ? LIMIT 1",
            (content_hash,),
        )
        return cur.fetchone() is not None

    def exists_by_id(self, recipe_id: str) -> bool:
        """True if a recipe with this slug id already exists."""
        cur = self._conn.execute(
            "SELECT 1 FROM recipes WHERE id = ? LIMIT 1", (recipe_id,)
        )
        return cur.fetchone() is not None

    # ---------------------------------------------------------------- status
    def set_status(self, recipe_id: str, status: str) -> None:
        """Advance a recipe's pipeline status."""
        self._conn.execute(
            "UPDATE recipes SET status = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (status, recipe_id),
        )
        self._conn.commit()

    def set_safety(
        self, recipe_id: str, toxic_flags: list[str], dog_safe: bool
    ) -> None:
        """Record safety-check results on a recipe."""
        self._conn.execute(
            "UPDATE recipes SET toxic_flags = ?, dog_safe = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (
                json.dumps(toxic_flags, ensure_ascii=False),
                1 if dog_safe else 0,
                recipe_id,
            ),
        )
        self._conn.commit()

    def set_publish_status(
        self, recipe_id: str, publish_status: dict[str, dict[str, str]]
    ) -> None:
        """Store per-channel publish status (wp/pdf/ig/fb) + flat URLs."""
        wp_url, ig_url, fb_url = _channel_urls(publish_status)
        self._conn.execute(
            "UPDATE recipes SET publish_status = ?, wp_url = ?, ig_url = ?, "
            "fb_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (
                json.dumps(publish_status, ensure_ascii=False),
                wp_url, ig_url, fb_url, recipe_id,
            ),
        )
        self._conn.commit()

    def set_season_tags(self, recipe_id: str, season_tags: list[str]) -> None:
        """Store the seasons a recipe suits (subset of pipeline.seasons.SEASONS).

        Empty list = all-season. Written by the seasonal-selection phase.
        """
        self._conn.execute(
            "UPDATE recipes SET season_tags = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(season_tags, ensure_ascii=False), recipe_id),
        )
        self._conn.commit()

    def set_affiliate_products(
        self, recipe_id: str, products: list[dict[str, str]]
    ) -> None:
        """Store matched affiliate products (list of {key, asin, display}).

        Written by the affiliate-matching phase; empty list = no matches.
        """
        self._conn.execute(
            "UPDATE recipes SET affiliate_products = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(products, ensure_ascii=False), recipe_id),
        )
        self._conn.commit()

    def set_generated_content(
        self, recipe_id: str, content: dict[str, str], content_status: str
    ) -> None:
        """Store generated draft content and advance the content-status lifecycle."""
        self._conn.execute(
            "UPDATE recipes SET generated_content = ?, content_status = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(content, ensure_ascii=False), content_status, recipe_id),
        )
        self._conn.commit()

    def set_content_status(self, recipe_id: str, content_status: str) -> None:
        """Advance the publish-content lifecycle state (see models.ContentStatus)."""
        self._conn.execute(
            "UPDATE recipes SET content_status = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (content_status, recipe_id),
        )
        self._conn.commit()

    def set_publish_results(
        self, recipe_id: str, results: list[dict[str, str]]
    ) -> None:
        """Store per-attempt publish outcomes (the local outcome log)."""
        self._conn.execute(
            "UPDATE recipes SET publish_results = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(results, ensure_ascii=False), recipe_id),
        )
        self._conn.commit()

    def list_by_content_status(self, content_status: str) -> list[RecipeRow]:
        """List recipes in a given content-lifecycle state, ordered by id."""
        cur = self._conn.execute(
            "SELECT * FROM recipes WHERE content_status = ? ORDER BY id",
            (content_status,),
        )
        return [self._row_to_recipe(r) for r in cur.fetchall()]

    def set_display_name(self, recipe_id: str, display_name: str) -> None:
        """Store the brand-voice display name shown in place of the source title."""
        self._conn.execute(
            "UPDATE recipes SET display_name = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (display_name, recipe_id),
        )
        self._conn.commit()

    def set_artifacts_path(self, recipe_id: str, artifacts_path: str) -> None:
        """Store the recipe's local artifact folder (relative to BRAND_DIR)."""
        self._conn.execute(
            "UPDATE recipes SET artifacts_path = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (artifacts_path, recipe_id),
        )
        self._conn.commit()

    def set_card(self, recipe_id: str, card_path: str) -> None:
        """Record that the static recipe card was created, with its file path.

        Stores the BRAND_DIR-relative ``card_path`` and stamps
        ``card_created_at`` so consumers can flag "card ready" and link the file.
        """
        self._conn.execute(
            "UPDATE recipes SET card_path = ?, card_created_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (card_path, recipe_id),
        )
        self._conn.commit()

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _row_to_recipe(sql_row: sqlite3.Row) -> RecipeRow:
        """Deserialize a SQL row (JSON columns) into a RecipeRow."""
        ingredients = [
            Ingredient(**ing)
            for ing in json.loads(sql_row["ingredients"] or "[]")
        ]
        return RecipeRow(
            id=sql_row["id"],
            name=sql_row["name"] or sql_row["title"],
            display_name=(
                sql_row["display_name"]
                # sqlite3.Row `in` checks values, so .keys() is required.
                if "display_name" in sql_row.keys()  # noqa: SIM118
                else ""
            )
            or "",
            artifacts_path=(
                sql_row["artifacts_path"]
                if "artifacts_path" in sql_row.keys()  # noqa: SIM118
                else ""
            )
            or "",
            card_path=(
                sql_row["card_path"]
                if "card_path" in sql_row.keys()  # noqa: SIM118
                else ""
            )
            or "",
            card_created_at=(
                sql_row["card_created_at"]
                if "card_created_at" in sql_row.keys()  # noqa: SIM118
                else ""
            )
            or "",
            wp_url=(
                sql_row["wp_url"]
                if "wp_url" in sql_row.keys()  # noqa: SIM118
                else ""
            )
            or "",
            ig_url=(
                sql_row["ig_url"]
                if "ig_url" in sql_row.keys()  # noqa: SIM118
                else ""
            )
            or "",
            fb_url=(
                sql_row["fb_url"]
                if "fb_url" in sql_row.keys()  # noqa: SIM118
                else ""
            )
            or "",
            ingredients=ingredients,
            steps=json.loads(sql_row["steps"] or "[]"),
            prep_minutes=sql_row["prep_minutes"] or 0,
            cook_minutes=sql_row["cook_minutes"] or 0,
            total_minutes=sql_row["total_minutes"] or 0,
            servings=sql_row["servings"] or "",
            nutrition=json.loads(sql_row["nutrition"] or "{}"),
            category=sql_row["category"] or "",
            tags=json.loads(sql_row["tags"] or "[]"),
            hero_image_url=sql_row["hero_image_url"] or "",
            source_url=sql_row["source_url"] or "",
            source_name=sql_row["source_name"] or "",
            license=sql_row["license"] or "",
            content_hash=sql_row["content_hash"] or "",
            publish_status=json.loads(
                (
                    sql_row["publish_status"]
                    # sqlite3.Row `in` checks values, so .keys() is required.
                    if "publish_status" in sql_row.keys()  # noqa: SIM118
                    else None
                )
                or "{}"
            ),
            status=sql_row["status"],
            toxic_flags=json.loads(sql_row["toxic_flags"] or "[]"),
            dog_safe=bool(sql_row["dog_safe"]),
            override=bool(sql_row["override"]),
            season_tags=json.loads(
                (
                    sql_row["season_tags"]
                    # sqlite3.Row `in` checks values, so .keys() is required.
                    if "season_tags" in sql_row.keys()  # noqa: SIM118
                    else None
                )
                or "[]"
            ),
            affiliate_products=json.loads(
                (
                    sql_row["affiliate_products"]
                    if "affiliate_products" in sql_row.keys()  # noqa: SIM118
                    else None
                )
                or "[]"
            ),
            generated_content=json.loads(
                (
                    sql_row["generated_content"]
                    if "generated_content" in sql_row.keys()  # noqa: SIM118
                    else None
                )
                or "{}"
            ),
            content_status=(
                sql_row["content_status"]
                if "content_status" in sql_row.keys()  # noqa: SIM118
                else None
            )
            or "none",
            publish_results=json.loads(
                (
                    sql_row["publish_results"]
                    if "publish_results" in sql_row.keys()  # noqa: SIM118
                    else None
                )
                or "[]"
            ),
        )
