"""Data-access layer for the engagements table (local Postgres backend).

Same public API as the earlier Supabase/SQLite versions — swap is transparent
to callers. Reads/writes go through ``lib.db`` (pooled psycopg connections),
the local-Postgres successor to ``lib.supabase_client.get_client()``.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib import db
from lib.engagements_db.models import ENGAGEMENT_COLUMNS, dedup_id

logger = logging.getLogger(__name__)


def _brand_id() -> str:
    brand_dir = os.environ.get("BRAND_DIR")
    return Path(brand_dir).name if brand_dir else "default"


class EngagementsRepository:
    """CRUD over the engagements table, dict-native to match the codebase."""

    def __init__(self, conn: object | None = None) -> None:
        pass  # conn ignored; pooled connections are obtained per-call via lib.db

    # --------------------------------------------------------------- writes
    def record(self, rec: dict[str, Any]) -> str:
        """Upsert one published post/comment. Returns its id."""
        platform = str(rec.get("platform", "")).strip()
        kind = str(rec.get("kind", "")).strip()
        if not platform or not kind:
            raise ValueError("engagement record requires 'platform' and 'kind'")

        ref = str(
            rec.get("ref")
            or rec.get("source_ref")
            or rec.get("permalink")
            or rec.get("target_url")
            or ""
        ).strip()
        eid = dedup_id(platform, kind, ref)
        posted_at = str(rec.get("posted_at") or datetime.now(UTC).isoformat())

        values: dict[str, Any] = {col: rec.get(col, "") for col in ENGAGEMENT_COLUMNS}
        values["platform"] = platform
        values["kind"] = kind
        values["status"] = rec.get("status") or "posted"
        values["posted_at"] = posted_at

        row: dict[str, Any] = {
            "id": eid,
            "brand_id": _brand_id(),
            **values,
        }

        columns = list(row.keys())
        insert_cols = ", ".join(columns)
        insert_placeholders = ", ".join(f"%({c})s" for c in columns)
        update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != "id")
        query = (
            f"INSERT INTO engagements ({insert_cols}) VALUES ({insert_placeholders}) "
            f"ON CONFLICT (id) DO UPDATE SET {update_clause}"
        )
        db.execute(query, row)
        return eid

    # ---------------------------------------------------------------- reads
    def list_engagements(
        self,
        *,
        platform: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Most-recent-first rows, optionally filtered by platform/kind/status."""
        clauses: list[str] = []
        params: list[Any] = []
        if platform:
            clauses.append("platform = %s")
            params.append(platform)
        if kind:
            clauses.append("kind = %s")
            params.append(kind)
        if status:
            clauses.append("status = %s")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 1000)))
        query = f"SELECT * FROM engagements{where} ORDER BY posted_at DESC LIMIT %s"
        return db.fetch_all(query, tuple(params))

    def get(self, eid: str) -> dict[str, Any] | None:
        return db.fetch_one("SELECT * FROM engagements WHERE id = %s", (eid,))

    def posted_comment_post_ids(self, platform: str, post_ids: list[str]) -> set[str]:
        """Of ``post_ids``, which already have a POSTED comment recorded here."""
        rows = db.fetch_all(
            "SELECT id FROM engagements WHERE platform = %s AND kind = %s AND status = %s",
            (platform, "comment", "posted"),
        )
        posted_ids: set[str] = {str(r.get("id", "")) for r in rows}
        out: set[str] = set()
        for pid in post_ids:
            if pid and dedup_id(platform, "comment", pid) in posted_ids:
                out.add(pid)
        return out

    def counts(self) -> dict[str, int]:
        """Totals by ``{platform}:{kind}`` for posted rows."""
        rows = db.fetch_all("SELECT platform, kind FROM engagements WHERE status = %s", ("posted",))
        totals: dict[str, int] = {}
        for row in rows:
            key = f"{row.get('platform', '')}:{row.get('kind', '')}"
            totals[key] = totals.get(key, 0) + 1
        return totals
