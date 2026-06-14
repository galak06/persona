"""Data-access layer for the brand + FB groups tables.

Operates on the plain group dicts the rest of the codebase already uses (the
groups_tracker shape), so consumers swap ``json.load``/``json.dump`` for
``load_all``/``save_all`` with no restructuring. Known keys map to typed columns
(``GROUP_COLUMNS``); ``notes`` is a JSON list; any other keys round-trip through
the ``extra`` JSON column for perfect fidelity.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from lib.groups_db.models import _ALWAYS_KEYS, GROUP_COLUMNS, group_id_from_url

logger = logging.getLogger(__name__)


def _brand_identity() -> tuple[str, str, str, str]:
    """(id, name, persona, site_url) for the current brand, from config/BRAND_DIR.

    The id is the stable BRAND_DIR folder name (e.g. ``dogfoodandfun``); name /
    persona / site_url come from config when available.
    """
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

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._brand_id: str | None = None

    # --------------------------------------------------------------- brand
    def ensure_brand(self) -> str:
        """Idempotently seed the single brand row (FK target). Returns its id."""
        if self._brand_id is not None:
            return self._brand_id
        bid, name, persona, url = _brand_identity()
        self._conn.execute(
            "INSERT INTO brands (id, name, persona, site_url, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
            "persona=excluded.persona, site_url=excluded.site_url, "
            "updated_at=CURRENT_TIMESTAMP",
            (bid, name, persona, url),
        )
        self._conn.commit()
        self._brand_id = bid
        return bid

    # --------------------------------------------------------------- writes
    def upsert_group(self, group: dict[str, Any]) -> None:
        """Insert/replace one group from its dict (keyed by group_url)."""
        brand_id = self.ensure_brand()
        url = str(group.get("group_url", "")).strip()
        if not url:
            return
        gid = group_id_from_url(url)
        notes_json = json.dumps(group.get("notes", []), ensure_ascii=False)
        extra = {
            k: v for k, v in group.items() if k not in GROUP_COLUMNS and k != "notes"
        }
        cols = ["id", "brand_id", "notes", "extra", *GROUP_COLUMNS]
        vals: list[Any] = [gid, brand_id, notes_json, json.dumps(extra, ensure_ascii=False)]
        for col in GROUP_COLUMNS:
            vals.append(url if col == "group_url" else group.get(col, ""))
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
        self._conn.execute(
            f"INSERT INTO fb_groups ({', '.join(cols)}, created_at, updated_at) "
            f"VALUES ({placeholders}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}, "
            f"created_at=COALESCE(fb_groups.created_at, CURRENT_TIMESTAMP), "
            f"updated_at=CURRENT_TIMESTAMP",
            vals,
        )
        self._conn.commit()

    def save_all(self, groups: list[dict[str, Any]]) -> None:
        """Upsert every group (drop-in for ``write_text(json.dumps(...))``).

        Upsert-only (no deletes) — matches all current writers, which mutate the
        array in place and never remove groups.
        """
        self.ensure_brand()
        for group in groups:
            self.upsert_group(group)

    def _set(self, group_url: str, column: str, value: str) -> bool:
        cur = self._conn.execute(
            f"UPDATE fb_groups SET {column}=?, updated_at=CURRENT_TIMESTAMP "
            f"WHERE group_url=?",
            (value, group_url),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def set_status(self, group_url: str, status: str) -> bool:
        return self._set(group_url, "status", status)

    def set_posting_mode(self, group_url: str, mode: str) -> bool:
        return self._set(group_url, "posting_mode", mode)

    def append_note(self, group_url: str, note: dict[str, str]) -> bool:
        """Append one {at, text} note to a group's JSON notes list (never clobbers)."""
        row = self._conn.execute(
            "SELECT notes FROM fb_groups WHERE group_url=?", (group_url,)
        ).fetchone()
        if row is None:
            return False
        notes = json.loads(row["notes"] or "[]")
        notes.append(note)
        return self._set(group_url, "notes", json.dumps(notes, ensure_ascii=False))

    # ---------------------------------------------------------------- reads
    def load_all(self) -> list[dict[str, Any]]:
        """All groups as tracker-shaped dicts (drop-in for ``json.loads(...)``)."""
        rows = self._conn.execute(
            "SELECT * FROM fb_groups ORDER BY group_name"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_groups(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is None:
            return self.load_all()
        rows = self._conn.execute(
            "SELECT * FROM fb_groups WHERE status=? ORDER BY group_name", (status,)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_by_url(self, group_url: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM fb_groups WHERE group_url=?", (group_url,)
        ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def get_by_name(self, group_name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM fb_groups WHERE group_name=?", (group_name,)
        ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """Reconstruct the tracker-shaped dict: non-empty typed columns + the
        always-keys + notes list + merged ``extra`` keys."""
        out: dict[str, Any] = {}
        for col in GROUP_COLUMNS:
            value = row[col] if col in row.keys() else ""  # noqa: SIM118
            if value not in ("", None) or col in _ALWAYS_KEYS:
                out[col] = value if value is not None else ""
        out["notes"] = json.loads((row["notes"] if "notes" in row.keys() else None) or "[]")  # noqa: SIM118
        extra = json.loads((row["extra"] if "extra" in row.keys() else None) or "{}")  # noqa: SIM118
        out.update(extra)
        return out
