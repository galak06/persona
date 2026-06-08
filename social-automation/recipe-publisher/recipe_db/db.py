"""SQLite connection + migration helpers for the recipe DB.

DB location resolves from the `BRAND_DIR` env var so the database lives in the
brand data dir, never in the engine repo. No secrets are read or stored here.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def resolve_db_path() -> Path:
    """Return the recipes.db path, creating its parent dir if needed.

    Prefers `${BRAND_DIR}/data/recipes.db`. Falls back to a path under the
    recipe-publisher package dir when `BRAND_DIR` is unset (logs a warning).
    """
    brand_dir = os.environ.get("BRAND_DIR")
    if brand_dir:
        db_path = Path(brand_dir) / "data" / "recipes.db"
    else:
        fallback_root = Path(__file__).resolve().parent.parent
        db_path = fallback_root / "data" / "recipes.db"
        logger.warning(
            "BRAND_DIR unset; falling back to %s for recipe DB", db_path
        )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with Row factory, WAL journaling, and FK enforcement."""
    db_path = path if path is not None else resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Columns added after the initial schema shipped. Backfilled via ALTER TABLE on
# pre-existing databases (CREATE TABLE IF NOT EXISTS never alters live tables).
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "recipes": {
        "publish_status": "TEXT DEFAULT '{}'",
        "display_name": "TEXT DEFAULT ''",
        "artifacts_path": "TEXT DEFAULT ''",
        "wp_url": "TEXT DEFAULT ''",
        "ig_url": "TEXT DEFAULT ''",
        "fb_url": "TEXT DEFAULT ''",
    },
}

# Columns removed after they shipped. Dropped from pre-existing databases.
_DROPPED_COLUMNS: dict[str, tuple[str, ...]] = {
    "recipes": ("rating_value", "rating_count"),
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add missing columns and drop retired ones on existing tables (idempotent)."""
    for table, columns in _ADDED_COLUMNS.items():
        existing = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                logger.info("added column %s.%s", table, name)
    for table, names in _DROPPED_COLUMNS.items():
        existing = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for name in names:
            if name in existing:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {name}")
                logger.info("dropped column %s.%s", table, name)


def migrate(conn: sqlite3.Connection) -> None:
    """Apply schema.sql then reconcile late-added/removed columns. Idempotent."""
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    _ensure_columns(conn)
    conn.commit()
    logger.info("recipe DB schema migrated")
