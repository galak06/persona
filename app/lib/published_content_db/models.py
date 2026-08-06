"""Data contracts for the published_content DB layer.

Mirrors `lib.engagements_db.models`' shape (typed column tuple + a slug-based
dedup id) and `lib.brands_db.models`' newer, leaner file split (no legacy
SQLite `db.py` shim -- this table has always lived in Postgres, see
`app/db/schema.sql`).
"""

from __future__ import annotations

import re

# Dict keys that map to typed `published_content` columns beyond the primary
# key. Kept here so the repository's INSERT column list has one source.
PUBLISHED_CONTENT_COLUMNS: tuple[str, ...] = (
    "wp_url",
    "wp_post_id",
    "gsc_query",
    "position",
    "impressions",
    "clicks",
    "ctr",
    "fetched_at",
)


def slugify(text: str) -> str:
    """Lowercase, hyphenate, strip non-alphanumerics."""
    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-")


def dedup_id(brand_id: str, wp_url: str, gsc_query: str) -> str:
    """Stable PK for one (brand, URL, query) row: ``slug({brand_id}:{wp_url}:{gsc_query})``.

    Unlike `engagements_db.models.dedup_id`, `brand_id` IS part of the key --
    two different brands can legitimately rank for the identical query text
    against different URLs, and the id must not collide across brands sharing
    this one table.
    """
    return slugify(f"{brand_id}:{wp_url}:{gsc_query}")
