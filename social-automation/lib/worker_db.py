"""Lightweight SQLite helper for recording worker run status.

Lives at ``${brand_dir}/data/db/workers.db``. Stores one row per
(worker_label, brand) pair — upserted on each run. No logging boilerplate;
callers own their own loggers.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS worker_runs (
    worker_label TEXT NOT NULL,
    brand        TEXT NOT NULL,
    status       TEXT NOT NULL,
    last_run     TEXT NOT NULL,
    message      TEXT DEFAULT '',
    PRIMARY KEY (worker_label, brand)
);
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _db_path(brand_dir: str | Path) -> Path:
    path = Path(brand_dir) / "data" / "db" / "workers.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect(brand_dir: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path(brand_dir)))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_CREATE_TABLE)
    conn.commit()
    return conn


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_start(brand_dir: str | Path, label: str, brand: str) -> None:
    """Upsert status='running', last_run=now UTC."""
    with _connect(brand_dir) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO worker_runs
                (worker_label, brand, status, last_run, message)
            VALUES (?, ?, 'running', ?, '')
            """,
            (label, brand, _now_utc()),
        )


def record_complete(
    brand_dir: str | Path,
    label: str,
    brand: str,
    status: str,
    message: str = "",
) -> None:
    """Upsert final status + last_run=now UTC.

    ``status`` should be 'success' or 'error'.
    """
    with _connect(brand_dir) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO worker_runs
                (worker_label, brand, status, last_run, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (label, brand, status, _now_utc(), message),
        )


def get_all(brand_dir: str | Path, brand: str) -> list[dict]:
    """Return all rows for this brand as a list of dicts."""
    with _connect(brand_dir) as conn:
        rows = conn.execute(
            "SELECT * FROM worker_runs WHERE brand = ? ORDER BY last_run DESC",
            (brand,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_one(brand_dir: str | Path, label: str, brand: str) -> dict | None:
    """Return one row dict or None if not found."""
    with _connect(brand_dir) as conn:
        row = conn.execute(
            "SELECT * FROM worker_runs WHERE worker_label = ? AND brand = ?",
            (label, brand),
        ).fetchone()
    return dict(row) if row else None
