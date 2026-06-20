"""Data-access layer for the engagements table (Supabase backend).

Same public API as the SQLite version — swap is transparent to callers.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.engagements_db.models import ENGAGEMENT_COLUMNS, dedup_id

logger = logging.getLogger(__name__)


def _brand_id() -> str:
    brand_dir = os.environ.get("BRAND_DIR")
    return Path(brand_dir).name if brand_dir else "default"


def _as_rows(data: Any) -> list[Any]:
    """Normalise supabase result.data (JSON union) to a plain list."""
    return list(data) if data else []


class EngagementsRepository:
    """CRUD over the engagements table, dict-native to match the codebase."""

    def __init__(self, conn: object | None = None) -> None:
        pass  # conn ignored; Supabase client is obtained per-call

    # --------------------------------------------------------------- writes
    def record(self, rec: dict[str, Any]) -> str:
        """Upsert one published post/comment. Returns its id."""
        from lib.supabase_client import get_client

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
            **{c: values[c] for c in ENGAGEMENT_COLUMNS},
        }

        client = get_client()
        client.table("engagements").upsert(row, on_conflict="id").execute()
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
        from lib.supabase_client import get_client

        q = get_client().table("engagements").select("*")
        if platform:
            q = q.eq("platform", platform)
        if kind:
            q = q.eq("kind", kind)
        if status:
            q = q.eq("status", status)
        q = q.order("posted_at", desc=True).limit(max(1, min(limit, 1000)))
        return [dict(r) for r in _as_rows(q.execute().data)]

    def get(self, eid: str) -> dict[str, Any] | None:
        from lib.supabase_client import get_client

        result = get_client().table("engagements").select("*").eq("id", eid).limit(1).execute()
        rows = _as_rows(result.data)
        return dict(rows[0]) if rows else None

    def posted_comment_post_ids(self, platform: str, post_ids: list[str]) -> set[str]:
        """Of ``post_ids``, which already have a POSTED comment recorded here."""
        from lib.supabase_client import get_client

        result = (
            get_client()
            .table("engagements")
            .select("id")
            .eq("platform", platform)
            .eq("kind", "comment")
            .eq("status", "posted")
            .execute()
        )
        posted_ids: set[str] = {str(dict(r).get("id", "")) for r in _as_rows(result.data)}
        out: set[str] = set()
        for pid in post_ids:
            if pid and dedup_id(platform, "comment", pid) in posted_ids:
                out.add(pid)
        return out

    def counts(self) -> dict[str, int]:
        """Totals by ``{platform}:{kind}`` for posted rows."""
        from lib.supabase_client import get_client

        result = (
            get_client()
            .table("engagements")
            .select("platform,kind")
            .eq("status", "posted")
            .execute()
        )
        totals: dict[str, int] = {}
        for item in _as_rows(result.data):
            row: dict[str, Any] = dict(item)
            key = f"{row.get('platform', '')}:{row.get('kind', '')}"
            totals[key] = totals.get(key, 0) + 1
        return totals
