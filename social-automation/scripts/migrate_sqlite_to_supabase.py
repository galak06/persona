"""One-time migration: 5 SQLite databases → Supabase.

Run AFTER creating the schema in Supabase SQL Editor (scripts/create_supabase_schema.sql):

    cd /path/to/social-automation
    BRAND_DIR=/path/to/dogfoodandfun python scripts/migrate_sqlite_to_supabase.py
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.supabase_client import get_client

_log = logging.getLogger(__name__)

BRAND_DIR = Path(
    os.environ.get(
        "BRAND_DIR",
        str(Path(__file__).resolve().parents[2] / "dogfoodandfun"),
    )
)
DB_DIR = BRAND_DIR / "data" / "db"


def _j(v: str | bytes | dict | list | None) -> object:
    """Parse JSON text from SQLite; return as-is if already a Python object."""
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


def _rows(path: Path, sql: str) -> list[dict]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    conn.close()
    return rows


def _upsert_batch(table: str, rows: list[dict], *, on_conflict: str | None = None) -> None:
    if not rows:
        return
    client = get_client()
    for i in range(0, len(rows), 100):
        batch = rows[i : i + 100]
        if on_conflict:
            client.table(table).upsert(batch, on_conflict=on_conflict).execute()
        else:
            client.table(table).upsert(batch).execute()


# ────────────────────────────────────────────────────────────────────────────


def migrate_schedule() -> None:
    path = DB_DIR / "schedule.db"
    if not path.exists():
        _log.warning("SKIP: %s not found", path)
        return
    json_cols = {"args", "depends_on", "schedule", "inputs", "extra"}
    rows = _rows(path, "SELECT * FROM schedule_tasks")
    for row in rows:
        for col in json_cols:
            if row.get(col) is not None:
                row[col] = _j(row[col])
    _upsert_batch("schedule_tasks", rows)
    _log.info("schedule_tasks: %d rows migrated", len(rows))


def migrate_workers() -> None:
    path = DB_DIR / "workers.db"
    if not path.exists():
        _log.warning("SKIP: %s not found", path)
        return
    rows = _rows(path, "SELECT * FROM worker_runs")
    _upsert_batch("worker_runs", rows)
    _log.info("worker_runs: %d rows migrated", len(rows))


def migrate_engagements() -> None:
    path = DB_DIR / "engagements.db"
    if not path.exists():
        _log.warning("SKIP: %s not found", path)
        return
    rows = _rows(path, "SELECT * FROM engagements")
    _upsert_batch("engagements", rows)
    _log.info("engagements: %d rows migrated", len(rows))


def migrate_groups() -> None:
    path = DB_DIR / "groups.db"
    if not path.exists():
        _log.warning("SKIP: %s not found", path)
        return
    brands = _rows(path, "SELECT * FROM brands")
    _upsert_batch("brands", brands)
    _log.info("brands: %d rows migrated", len(brands))

    groups = _rows(path, "SELECT * FROM fb_groups")
    for row in groups:
        row["notes"] = _j(row.get("notes")) or []
        row["extra"] = _j(row.get("extra")) or {}
    _upsert_batch("fb_groups", groups)
    _log.info("fb_groups: %d rows migrated", len(groups))


def migrate_recipes() -> None:
    path = DB_DIR / "recipes.db"
    if not path.exists():
        _log.warning("SKIP: %s not found", path)
        return
    json_cols = {
        "ingredients", "steps", "nutrition", "tags", "toxic_flags",
        "publish_status", "affiliate_products", "generated_content",
        "publish_results", "season_tags",
    }

    scrapes = _rows(path, "SELECT * FROM raw_scrapes")
    for row in scrapes:
        row.pop("id", None)
        row["payload"] = _j(row.get("payload"))
    _upsert_batch("raw_scrapes", scrapes, on_conflict="content_hash")
    _log.info("raw_scrapes: %d rows migrated", len(scrapes))

    recipes = _rows(path, "SELECT * FROM recipes")
    for row in recipes:
        for col in json_cols:
            if row.get(col) is not None:
                row[col] = _j(row[col])
    _upsert_batch("recipes", recipes)
    _log.info("recipes: %d rows migrated", len(recipes))


# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _log.info("Migrating from %s → Supabase …", DB_DIR)
    migrate_schedule()
    migrate_workers()
    migrate_engagements()
    migrate_groups()
    migrate_recipes()
    _log.info("Done.")
