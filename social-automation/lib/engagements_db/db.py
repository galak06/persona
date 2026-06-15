"""SQLite connection + migration for the engagements DB.

Lives at ``${BRAND_DIR}/data/db/engagements.db`` — separate from groups.db and
recipes.db. Stores one row per published post/comment. Mirrors
lib/groups_db/db.py. No secrets are read or stored here.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def resolve_engagements_db_path() -> Path:
    """Return ``${BRAND_DIR}/data/db/engagements.db``, mkdir'ing its parent.

    Falls back under the package when BRAND_DIR is unset (tests pass an explicit
    path to ``connect``)."""
    brand_dir = os.environ.get("BRAND_DIR")
    if brand_dir:
        db_path = Path(brand_dir) / "data" / "db" / "engagements.db"
    else:
        fallback_root = Path(__file__).resolve().parent.parent.parent
        db_path = fallback_root / "data" / "db" / "engagements.db"
        logger.warning("BRAND_DIR unset; falling back to %s for engagements DB", db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with Row factory, WAL journaling, and FK enforcement."""
    db_path = path if path is not None else resolve_engagements_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Columns added after the initial schema shipped — backfilled via ALTER TABLE on
# existing databases (CREATE TABLE IF NOT EXISTS never alters live tables).
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "engagements": {},
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add any missing late-added columns on existing tables (idempotent)."""
    for table, columns in _ADDED_COLUMNS.items():
        existing = {
            str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                logger.info("added column %s.%s", table, name)


def migrate(conn: sqlite3.Connection) -> None:
    """Apply schema.sql then reconcile late-added columns. Idempotent."""
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    _ensure_columns(conn)
    conn.commit()
    logger.info("engagements DB schema migrated")
