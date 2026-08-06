"""Data-access layer for the published_content table (local Postgres backend).

Same shape as `lib.engagements_db.repository` -- deterministic upsert via
`ON CONFLICT`, dict-native rows, built on `lib.db`'s pooled `execute()` /
`fetch_all()` / `fetch_one()`. One row per (brand, published URL, GSC query)
tuple; `scripts/backfill_gsc_content.py` is the only writer today, and
`lib.gsc_scout` is the only reader besides ad hoc reporting.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from lib import db
from lib.published_content_db.models import dedup_id


class PublishedContentRepository:
    """CRUD over the published_content table, dict-native to match the codebase."""

    def __init__(self, conn: object | None = None) -> None:
        pass  # conn ignored; pooled connections are obtained per-call via lib.db

    # --------------------------------------------------------------- writes
    def upsert(self, rec: dict[str, Any]) -> str:
        """Upsert one (brand, URL, query) row. Returns its id.

        Required keys: ``brand_id``, ``wp_url``, ``gsc_query``. Numeric
        fields (``position``/``impressions``/``clicks``/``ctr``) default to
        0 when absent so a partial GSC row (e.g. zero clicks) never fails.
        """
        brand_id = str(rec.get("brand_id", "")).strip()
        wp_url = str(rec.get("wp_url", "")).strip()
        gsc_query = str(rec.get("gsc_query", "")).strip()
        if not brand_id or not wp_url or not gsc_query:
            raise ValueError("published_content row requires 'brand_id', 'wp_url', 'gsc_query'")

        row_id = dedup_id(brand_id, wp_url, gsc_query)
        now = datetime.now(UTC).isoformat()

        row: dict[str, Any] = {
            "id": row_id,
            "brand_id": brand_id,
            "wp_url": wp_url,
            "wp_post_id": str(rec.get("wp_post_id", "") or ""),
            "gsc_query": gsc_query,
            "position": float(rec.get("position", 0) or 0),
            "impressions": int(rec.get("impressions", 0) or 0),
            "clicks": int(rec.get("clicks", 0) or 0),
            "ctr": float(rec.get("ctr", 0) or 0),
            "fetched_at": str(rec.get("fetched_at") or now),
            "updated_at": now,
        }

        # created_at is stamped once from `updated_at`'s value at insert time
        # (reusing the same named param) and then deliberately left out of
        # the UPDATE SET clause below, so a re-upsert never overwrites it.
        columns = list(row.keys())
        insert_cols = ", ".join(columns)
        insert_placeholders = ", ".join(f"%({c})s" for c in columns)
        update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != "id")
        query = (
            f"INSERT INTO published_content ({insert_cols}, created_at) "
            f"VALUES ({insert_placeholders}, %(updated_at)s) "
            f"ON CONFLICT (id) DO UPDATE SET {update_clause}"
        )
        db.execute(query, row)
        return row_id

    # ---------------------------------------------------------------- reads
    def list_for_brand(
        self,
        brand_id: str,
        *,
        wp_url: str | None = None,
        min_position: float | None = None,
        max_position: float | None = None,
        min_impressions: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Rows for one brand, optionally filtered by URL/position band/impressions."""
        clauses: list[str] = ["brand_id = %s"]
        params: list[Any] = [brand_id]
        if wp_url is not None:
            clauses.append("wp_url = %s")
            params.append(wp_url)
        if min_position is not None:
            clauses.append("position >= %s")
            params.append(min_position)
        if max_position is not None:
            clauses.append("position <= %s")
            params.append(max_position)
        if min_impressions is not None:
            clauses.append("impressions >= %s")
            params.append(min_impressions)
        where = " AND ".join(clauses)
        params.append(max(1, min(limit, 10000)))
        query = f"SELECT * FROM published_content WHERE {where} ORDER BY impressions DESC LIMIT %s"
        return db.fetch_all(query, tuple(params))

    def get(self, row_id: str) -> dict[str, Any] | None:
        return db.fetch_one("SELECT * FROM published_content WHERE id = %s", (row_id,))
