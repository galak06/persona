"""Data-access layer for the brands table (local Postgres backend, via `lib/db.py`).

One code path writes brand identity: `ensure()` is the exact upsert-if-absent
shape `groups_db.ensure_brand()` used inline before this module existed
(`groups_db/repository.py` now delegates to it). `create()` is the separate,
stricter onboarding path (PR3) -- a real INSERT that rejects duplicates and
missing required fields instead of silently upserting.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from lib import db
from lib.brands_db.models import BrandStatus, default_enabled_flows

logger = logging.getLogger(__name__)


class BrandAlreadyExistsError(ValueError):
    """Raised by `create()` when a brand with the given id already exists."""


class BrandsRepository:
    """CRUD over the brands table, dict-native to match the codebase."""

    def __init__(self, conn: object | None = None) -> None:
        pass  # conn ignored; pooled connections are obtained per-call via lib.db

    # --------------------------------------------------------------- writes
    def create(
        self,
        *,
        brand_id: str,
        name: str,
        site_url: str,
        niche: str,
        persona: str = "",
        mascot_name: str = "",
        target_audience: str = "",
        keywords: dict[str, Any] | None = None,
        competitor_accounts: list[Any] | None = None,
        enabled_flows: list[str] | None = None,
        headless: bool = True,
        group_join_limit: int = 10,
        status: str = BrandStatus.DRAFT,
        brand_dir: str = "",
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Insert a new brand row. Returns its id.

        Unlike `ensure()`, this never upserts -- onboarding a brand whose id
        already exists is a caller bug (or a genuine name collision), not
        something to paper over. Raises `ValueError` if `name`/`site_url`/
        `niche` are missing, `BrandAlreadyExistsError` on duplicate id.
        """
        brand_id = (brand_id or "").strip()
        name = (name or "").strip()
        site_url = (site_url or "").strip()
        niche = (niche or "").strip()

        missing = [
            field
            for field, value in (
                ("id", brand_id),
                ("name", name),
                ("site_url", site_url),
                ("niche", niche),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"brands.create requires non-empty: {', '.join(missing)}")

        if status not in BrandStatus.ALL:
            raise ValueError(f"invalid brand status: {status!r}")

        row: dict[str, Any] = {
            "id": brand_id,
            "name": name,
            "persona": persona,
            "site_url": site_url,
            "niche": niche,
            "mascot_name": mascot_name,
            "target_audience": target_audience,
            "keywords": Jsonb(keywords if keywords is not None else {}),
            "competitor_accounts": Jsonb(
                competitor_accounts if competitor_accounts is not None else []
            ),
            "enabled_flows": Jsonb(
                enabled_flows if enabled_flows is not None else default_enabled_flows()
            ),
            "headless": headless,
            "group_join_limit": group_join_limit,
            "status": status,
            "brand_dir": brand_dir,
            "extra": Jsonb(extra if extra is not None else {}),
        }
        columns = list(row.keys())
        col_list = ", ".join(columns)
        placeholders = ", ".join(f"%({c})s" for c in columns)

        try:
            db.execute(f"INSERT INTO brands ({col_list}) VALUES ({placeholders})", row)
        except psycopg.errors.UniqueViolation as exc:
            raise BrandAlreadyExistsError(f"brand '{brand_id}' already exists") from exc
        return brand_id

    def ensure(self, brand_id: str, name: str, persona: str = "", site_url: str = "") -> str:
        """Idempotently seed a brand row's identity fields. Returns its id.

        Mirrors the exact upsert-if-absent semantics `groups_db.ensure_brand()`
        used before this module existed -- same four columns, same
        ON CONFLICT behavior. Every other column (niche, status, brand_dir,
        ...) is left at its schema default / current value; `ensure()` never
        touches them, so it is safe to call repeatedly from `groups_db` (or
        anywhere else that just needs the FK target to exist) without
        clobbering onboarding data written by `create()`.
        """
        bid = (brand_id or "").strip() or "default"
        db.execute(
            """
            INSERT INTO brands (id, name, persona, site_url)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                persona = EXCLUDED.persona,
                site_url = EXCLUDED.site_url
            """,
            (bid, name, persona, site_url),
        )
        return bid

    # ---------------------------------------------------------------- reads
    def get(self, brand_id: str) -> dict[str, Any] | None:
        return db.fetch_one("SELECT * FROM brands WHERE id = %s", (brand_id,))

    def list_brands(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None:
            return db.fetch_all("SELECT * FROM brands WHERE status = %s ORDER BY id", (status,))
        return db.fetch_all("SELECT * FROM brands ORDER BY id")

    # -------------------------------------------------------------- updates
    def update_status(self, brand_id: str, status: str) -> bool:
        if status not in BrandStatus.ALL:
            raise ValueError(f"invalid brand status: {status!r}")
        rowcount = db.execute("UPDATE brands SET status = %s WHERE id = %s", (status, brand_id))
        return rowcount > 0

    def set_brand_dir(self, brand_id: str, brand_dir: str) -> bool:
        rowcount = db.execute(
            "UPDATE brands SET brand_dir = %s WHERE id = %s", (brand_dir, brand_id)
        )
        return rowcount > 0

    def update(
        self,
        brand_id: str,
        *,
        headless: bool | None = None,
        keywords: dict[str, Any] | None = None,
        competitor_accounts: list[Any] | None = None,
        enabled_flows: list[str] | None = None,
        group_join_limit: int | None = None,
    ) -> bool:
        """Partial update -- only params passed a non-`None` value change.

        Settings-page edit path (`PATCH /api/v1/brands/{id}/settings`): a
        caller PATCHing just `headless` must never clobber this brand's
        `keywords`, and vice versa. `None` means "leave alone" for every
        param here (none of these columns are ever meaningfully set back to
        SQL NULL through this method); returns `False` with no query issued
        if every param is `None`.
        """
        updates: dict[str, Any] = {}
        if headless is not None:
            updates["headless"] = headless
        if keywords is not None:
            updates["keywords"] = Jsonb(keywords)
        if competitor_accounts is not None:
            updates["competitor_accounts"] = Jsonb(competitor_accounts)
        if enabled_flows is not None:
            updates["enabled_flows"] = Jsonb(enabled_flows)
        if group_join_limit is not None:
            updates["group_join_limit"] = group_join_limit

        if not updates:
            return False

        set_clause = ", ".join(f"{col} = %({col})s" for col in updates)
        updates["id"] = brand_id
        rowcount = db.execute(f"UPDATE brands SET {set_clause} WHERE id = %(id)s", updates)
        return rowcount > 0
