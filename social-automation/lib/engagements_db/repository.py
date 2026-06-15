"""Data-access layer for the engagements table.

Dict-native (like ``groups_db``): ``record`` upserts one publish keyed by a stable
``dedup_id`` so retries/failures-then-success collapse to one row; reads return
plain dicts for the API.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.engagements_db.models import ENGAGEMENT_COLUMNS, dedup_id

logger = logging.getLogger(__name__)


def _brand_id() -> str:
    """Stable brand slug = the BRAND_DIR folder name (e.g. ``dogfoodandfun``)."""
    brand_dir = os.environ.get("BRAND_DIR")
    return Path(brand_dir).name if brand_dir else "default"


class EngagementsRepository:
    """CRUD over the engagements table, dict-native to match the codebase."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --------------------------------------------------------------- writes
    def record(self, rec: dict[str, Any]) -> str:
        """Upsert one published post/comment. Returns its id.

        Required keys: ``platform``, ``kind``. ``ref`` (the natural key) defaults
        to source_ref / permalink / target_url when omitted. ``posted_at``
        defaults to now (UTC). Re-recording the same (platform, kind, ref) updates
        the row in place (e.g. failed → posted).
        """
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

        cols = ["id", "brand_id", *ENGAGEMENT_COLUMNS]
        vals: list[Any] = [eid, _brand_id(), *(values[c] for c in ENGAGEMENT_COLUMNS)]
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
        self._conn.execute(
            f"INSERT INTO engagements ({', '.join(cols)}, created_at, updated_at) "
            f"VALUES ({placeholders}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}, "
            f"created_at=COALESCE(engagements.created_at, CURRENT_TIMESTAMP), "
            f"updated_at=CURRENT_TIMESTAMP",
            vals,
        )
        self._conn.commit()
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
        for col, val in (("platform", platform), ("kind", kind), ("status", status)):
            if val:
                clauses.append(f"{col}=?")
                params.append(val)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 1000)))
        rows = self._conn.execute(
            f"SELECT * FROM engagements {where} ORDER BY posted_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def get(self, eid: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM engagements WHERE id=?", (eid,)
        ).fetchone()
        return dict(row) if row is not None else None

    def counts(self) -> dict[str, int]:
        """Totals by ``{platform}:{kind}`` for posted rows (for UI summaries)."""
        rows = self._conn.execute(
            "SELECT platform, kind, COUNT(*) AS n FROM engagements "
            "WHERE status='posted' GROUP BY platform, kind"
        ).fetchall()
        return {f"{r['platform']}:{r['kind']}": int(r["n"]) for r in rows}
