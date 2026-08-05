"""Postgres-backed reads for the `flow_templates` table.

Brand-agnostic flow catalog (`id`, `script`, `schedule.cron`, rate limits,
etc.) -- the DB-backed replacement for reading `profiles/*.json` directly.
`lib/brand_provisioning.py` reads from here when onboarding a new brand.
Seeded/reseeded via `scripts/backfill_flow_templates.py`, which remains the
one place that parses `profiles/*.json` -- everything else reads Postgres.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from lib import db

_JSON_COLUMNS = {"args", "depends_on", "schedule", "inputs"}
_BOOL_COLUMNS = {"requires_approval", "requires_browser", "re_run_guard", "telegram_notify"}
_COLUMNS = (
    "id",
    "platform",
    "title",
    "description",
    "order_num",
    "script",
    "skill",
    "args",
    "depends_on",
    "requires_approval",
    "approval_channel",
    "requires_browser",
    "re_run_guard",
    "output_file",
    "schedule",
    "inputs",
    "telegram_notify",
)


def _coerce(row: dict[str, Any]) -> dict[str, Any]:
    template = dict(row)
    for col in _BOOL_COLUMNS:
        if col in template and template[col] is not None:
            template[col] = bool(template[col])
    return template


def load_all() -> list[dict[str, Any]]:
    """Every flow template, ordered by platform then order_num."""
    rows = db.fetch_all("SELECT * FROM flow_templates ORDER BY platform ASC, order_num ASC")
    return [_coerce(r) for r in rows]


def get(flow_id: str) -> dict[str, Any] | None:
    """One flow template by id, or None if it isn't seeded."""
    rows = db.fetch_all("SELECT * FROM flow_templates WHERE id = %s", (flow_id,))
    return _coerce(rows[0]) if rows else None


def save(row: dict[str, Any]) -> None:
    """Upsert one flow template row by id."""
    values = dict(row)
    for col in _BOOL_COLUMNS:
        if col in values and values[col] is not None:
            values[col] = int(bool(values[col]))
    params = {
        col: Jsonb(values[col]) if col in _JSON_COLUMNS and values.get(col) is not None else values.get(col)
        for col in _COLUMNS
    }
    col_list = ", ".join(_COLUMNS)
    placeholders = ", ".join(f"%({col})s" for col in _COLUMNS)
    update_cols = [c for c in _COLUMNS if c != "id"]
    conflict = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    db.execute(
        f"INSERT INTO flow_templates ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT (id) DO UPDATE SET {conflict}",
        params,
    )
