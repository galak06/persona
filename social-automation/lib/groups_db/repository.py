"""Data-access layer for the brand + FB groups tables (Supabase backend).

Same public API as the SQLite version — swap is transparent to callers.
JSONB columns (notes, extra) come back as Python objects; no json.loads needed.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from lib.groups_db.models import _ALWAYS_KEYS, GROUP_COLUMNS, group_id_from_url

logger = logging.getLogger(__name__)


def _brand_identity() -> tuple[str, str, str, str]:
    """(id, name, persona, site_url) for the current brand."""
    brand_dir = os.environ.get("BRAND_DIR")
    bid = Path(brand_dir).name if brand_dir else "default"
    name, persona, url = bid, "", ""
    try:
        from lib.config import settings

        name = settings.site.name or bid
        persona = settings.site.brand_persona or ""
        url = settings.site.url or ""
    except Exception as exc:
        logger.debug("brand identity from config unavailable: %s", exc)
    return bid, name, persona, url


def _as_rows(data: Any) -> list[Any]:
    """Extract supabase result.data as a list (handles None and JSON union types)."""
    return list(data) if data else []


class GroupsRepository:
    """CRUD over the brand + fb_groups tables, dict-native to match the codebase."""

    def __init__(self, conn: object | None = None) -> None:
        self._brand_id: str | None = None

    # --------------------------------------------------------------- brand
    def ensure_brand(self) -> str:
        """Idempotently seed the single brand row (FK target). Returns its id."""
        if self._brand_id is not None:
            return self._brand_id
        from lib.supabase_client import get_client

        bid, name, persona, url = _brand_identity()
        get_client().table("brands").upsert(
            {"id": bid, "name": name, "persona": persona, "site_url": url},
            on_conflict="id",
        ).execute()
        self._brand_id = bid
        return bid

    # --------------------------------------------------------------- writes
    def upsert_group(self, group: dict[str, Any]) -> None:
        """Insert/replace one group from its dict (keyed by group_url)."""
        from lib.supabase_client import get_client

        brand_id = self.ensure_brand()
        url = str(group.get("group_url", "")).strip()
        if not url:
            return
        gid = group_id_from_url(url)
        notes = group.get("notes", [])
        extra = {k: v for k, v in group.items() if k not in GROUP_COLUMNS and k != "notes"}

        row: dict[str, Any] = {
            "id": gid,
            "brand_id": brand_id,
            "notes": notes,
            "extra": extra,
        }
        for col in GROUP_COLUMNS:
            row[col] = url if col == "group_url" else group.get(col, "")

        get_client().table("fb_groups").upsert(row, on_conflict="id").execute()

    def save_all(self, groups: list[dict[str, Any]]) -> None:
        """Upsert every group (drop-in for ``write_text(json.dumps(...))``).

        Upsert-only — matches all current writers which never remove groups.
        """
        self.ensure_brand()
        for group in groups:
            self.upsert_group(group)

    def _update_column(self, group_url: str, column: str, value: Any) -> bool:
        from lib.supabase_client import get_client

        result = (
            get_client()
            .table("fb_groups")
            .update({column: value})
            .eq("group_url", group_url)
            .execute()
        )
        return bool(result.data)

    def set_status(self, group_url: str, status: str) -> bool:
        return self._update_column(group_url, "status", status)

    def set_posting_mode(self, group_url: str, mode: str) -> bool:
        return self._update_column(group_url, "posting_mode", mode)

    def append_note(self, group_url: str, note: dict[str, str]) -> bool:
        """Append one {at, text} note to a group's notes list (never clobbers)."""
        from lib.supabase_client import get_client

        result = (
            get_client()
            .table("fb_groups")
            .select("notes")
            .eq("group_url", group_url)
            .limit(1)
            .execute()
        )
        rows = _as_rows(result.data)
        if not rows:
            return False
        row: dict[str, Any] = dict(rows[0])
        raw_notes = row.get("notes") or []
        if isinstance(raw_notes, str):
            raw_notes = json.loads(raw_notes)
        raw_notes.append(note)
        return self._update_column(group_url, "notes", raw_notes)

    # ---------------------------------------------------------------- reads
    def load_all(self) -> list[dict[str, Any]]:
        """All groups as tracker-shaped dicts."""
        from lib.supabase_client import get_client

        result = get_client().table("fb_groups").select("*").order("group_name").execute()
        return [self._row_to_dict(r) for r in _as_rows(result.data)]

    def list_groups(self, status: str | None = None) -> list[dict[str, Any]]:
        from lib.supabase_client import get_client

        q = get_client().table("fb_groups").select("*")
        if status is not None:
            q = q.eq("status", status)
        result = q.order("group_name").execute()
        return [self._row_to_dict(r) for r in _as_rows(result.data)]

    def get_by_url(self, group_url: str) -> dict[str, Any] | None:
        from lib.supabase_client import get_client

        result = (
            get_client().table("fb_groups").select("*").eq("group_url", group_url).limit(1).execute()
        )
        rows = _as_rows(result.data)
        return self._row_to_dict(rows[0]) if rows else None

    def get_by_name(self, group_name: str) -> dict[str, Any] | None:
        from lib.supabase_client import get_client

        result = (
            get_client().table("fb_groups").select("*").eq("group_name", group_name).limit(1).execute()
        )
        rows = _as_rows(result.data)
        return self._row_to_dict(rows[0]) if rows else None

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """Reconstruct the tracker-shaped dict from a Supabase row."""
        r: dict[str, Any] = dict(row)
        out: dict[str, Any] = {}
        for col in GROUP_COLUMNS:
            value = r.get(col, "")
            if value not in ("", None) or col in _ALWAYS_KEYS:
                out[col] = value if value is not None else ""
        raw_notes = r.get("notes") or []
        out["notes"] = json.loads(raw_notes) if isinstance(raw_notes, str) else raw_notes
        raw_extra = r.get("extra") or {}
        extra: dict[str, Any] = json.loads(raw_extra) if isinstance(raw_extra, str) else raw_extra
        out.update(extra)
        return out
