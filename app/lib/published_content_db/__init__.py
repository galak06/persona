"""Published-URL <-> GSC-query store (``published_content`` table, via ``lib/db.py``).

The anti-cannibalization/opportunity-scoring data source for `lib.gsc_scout`.
Module-level functions mirror ``brands_db``/``engagements_db``'s compat-layer
pattern: each opens a short-lived ``PublishedContentRepository`` and delegates.

    from lib import published_content_db

    published_content_db.upsert_row(
        {"brand_id": "dogfoodandfun", "wp_url": "...", "gsc_query": "..."}
    )
    rows = published_content_db.list_for_brand("dogfoodandfun", min_position=6, max_position=25)
"""

from __future__ import annotations

from typing import Any

from lib.published_content_db.models import dedup_id
from lib.published_content_db.repository import PublishedContentRepository

__all__ = [
    "PublishedContentRepository",
    "dedup_id",
    "get",
    "list_for_brand",
    "upsert_row",
]


def _repo() -> PublishedContentRepository:
    return PublishedContentRepository(None)


def upsert_row(rec: dict[str, Any]) -> str:
    return _repo().upsert(rec)


def list_for_brand(
    brand_id: str,
    *,
    wp_url: str | None = None,
    min_position: float | None = None,
    max_position: float | None = None,
    min_impressions: int | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    return _repo().list_for_brand(
        brand_id,
        wp_url=wp_url,
        min_position=min_position,
        max_position=max_position,
        min_impressions=min_impressions,
        limit=limit,
    )


def get(row_id: str) -> dict[str, Any] | None:
    return _repo().get(row_id)
