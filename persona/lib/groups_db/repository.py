"""Data-access layer for the brand + FB groups tables (local Postgres backend).

Same public API/behavior as the earlier Supabase version — swap is transparent
to callers. JSONB columns (``notes``, ``extra``) round-trip as native Python
objects: psycopg parses them automatically on read, and writes wrap dict/list
values in ``psycopg.types.json.Jsonb`` so they serialize correctly.

Column/table names interpolated into SQL below (``GROUP_COLUMNS``, the fixed
``"status"``/``"posting_mode"``/``"notes"`` set) are static, code-defined
constants — never user input — so plain f-string composition is safe here.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from lib import brands_db
from lib.db import execute, fetch_all, fetch_one
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


class GroupsRepository:
    """CRUD over the brand + fb_groups tables, dict-native to match the codebase."""

    def __init__(self, conn: object | None = None) -> None:
        self._brand_id: str | None = None

    # --------------------------------------------------------------- brand
    def ensure_brand(self) -> str:
        """Idempotently seed the single brand row (FK target). Returns its id.

        Delegates to `brands_db.ensure()` -- one code path writes brand
        identity. Behavior is unchanged: same `id = Path(BRAND_DIR).name`
        convention, same upsert-if-absent semantics on the same four columns.
        """
        if self._brand_id is not None:
            return self._brand_id

        bid, name, persona, url = _brand_identity()
        self._brand_id = brands_db.ensure(bid, name, persona, url)
        return self._brand_id

    # --------------------------------------------------------------- writes
    def upsert_group(self, group: dict[str, Any]) -> None:
        """Insert/replace one group from its dict (keyed by group_url)."""
        brand_id = self.ensure_brand()
        url = str(group.get("group_url", "")).strip()
        if not url:
            return
        gid = group_id_from_url(url)
        notes = group.get("notes", [])
        extra = {k: v for k, v in group.items() if k not in GROUP_COLUMNS and k != "notes"}

        row: dict[str, Any] = {"id": gid, "brand_id": brand_id}
        for col in GROUP_COLUMNS:
            row[col] = url if col == "group_url" else group.get(col, "")

        columns = ["id", "brand_id", *GROUP_COLUMNS, "notes", "extra"]
        values: list[Any] = [row[c] for c in ("id", "brand_id", *GROUP_COLUMNS)]
        values.extend([Jsonb(notes), Jsonb(extra)])
        placeholders = ", ".join(["%s"] * len(columns))
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != "id")

        execute(
            f"INSERT INTO fb_groups ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT (id) DO UPDATE SET {set_clause}",
            values,
        )

    def save_all(self, groups: list[dict[str, Any]]) -> None:
        """Upsert every group (drop-in for ``write_text(json.dumps(...))``).

        Upsert-only — matches all current writers which never remove groups.
        """
        self.ensure_brand()
        for group in groups:
            self.upsert_group(group)

    def _update_column(self, group_url: str, column: str, value: Any) -> bool:
        param = Jsonb(value) if isinstance(value, (list, dict)) else value
        rowcount = execute(
            f"UPDATE fb_groups SET {column} = %s WHERE group_url = %s",
            (param, group_url),
        )
        return rowcount > 0

    def set_status(self, group_url: str, status: str) -> bool:
        return self._update_column(group_url, "status", status)

    def set_posting_mode(self, group_url: str, mode: str) -> bool:
        return self._update_column(group_url, "posting_mode", mode)

    def append_note(self, group_url: str, note: dict[str, str]) -> bool:
        """Append one {at, text} note to a group's notes list (never clobbers)."""
        row = fetch_one("SELECT notes FROM fb_groups WHERE group_url = %s", (group_url,))
        if row is None:
            return False
        notes = list(row.get("notes") or [])
        notes.append(note)
        return self._update_column(group_url, "notes", notes)

    # ---------------------------------------------------------------- reads
    def load_all(self) -> list[dict[str, Any]]:
        """All groups as tracker-shaped dicts."""
        rows = fetch_all("SELECT * FROM fb_groups ORDER BY group_name")
        return [self._row_to_dict(r) for r in rows]

    def list_groups(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None:
            rows = fetch_all(
                "SELECT * FROM fb_groups WHERE status = %s ORDER BY group_name", (status,)
            )
        else:
            rows = fetch_all("SELECT * FROM fb_groups ORDER BY group_name")
        return [self._row_to_dict(r) for r in rows]

    def get_by_url(self, group_url: str) -> dict[str, Any] | None:
        row = fetch_one("SELECT * FROM fb_groups WHERE group_url = %s", (group_url,))
        return self._row_to_dict(row) if row is not None else None

    def get_by_name(self, group_name: str) -> dict[str, Any] | None:
        row = fetch_one("SELECT * FROM fb_groups WHERE group_name = %s", (group_name,))
        return self._row_to_dict(row) if row is not None else None

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """Reconstruct the tracker-shaped dict from a Postgres row."""
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
