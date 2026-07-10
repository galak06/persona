"""Postgres-backed helper for recording worker run status.

Stores one row per (worker_label, brand) pair in the `worker_runs` table via
`lib/db.py`. The brand_dir parameter is still accepted for backward compat
(used only for PID file cleanup in record_complete).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib import db


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


_UPSERT_SQL = """
    INSERT INTO worker_runs (worker_label, brand, status, last_run, message)
    VALUES (%(worker_label)s, %(brand)s, %(status)s, %(last_run)s, %(message)s)
    ON CONFLICT (worker_label, brand) DO UPDATE SET
        status = EXCLUDED.status,
        last_run = EXCLUDED.last_run,
        message = EXCLUDED.message
"""


def record_start(brand_dir: str | Path, label: str, brand: str) -> None:
    """Upsert status='running', last_run=now UTC."""
    db.execute(
        _UPSERT_SQL,
        {
            "worker_label": label,
            "brand": brand,
            "status": "running",
            "last_run": _now_utc(),
            "message": "",
        },
    )


def record_complete(
    brand_dir: str | Path,
    label: str,
    brand: str,
    status: str,
    message: str = "",
) -> None:
    """Upsert final status + last_run=now UTC.

    Also removes any PID files so the next trigger isn't falsely blocked.
    """
    db.execute(
        _UPSERT_SQL,
        {
            "worker_label": label,
            "brand": brand,
            "status": status,
            "last_run": _now_utc(),
            "message": message,
        },
    )

    logs_dir = Path(brand_dir) / "logs"
    suffix = label.removeprefix("com.persona.").replace("-", "_")
    for pattern in (f"{suffix}.pid", f"{suffix}_0.pid", f"{suffix}_1.pid", f"{suffix}_2.pid"):
        p = logs_dir / pattern
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def get_all(brand_dir: str | Path, brand: str) -> list[dict[str, Any]]:
    """Return all rows for this brand as a list of dicts."""
    return db.fetch_all(
        "SELECT * FROM worker_runs WHERE brand = %s ORDER BY last_run DESC",
        (brand,),
    )


def get_one(brand_dir: str | Path, label: str, brand: str) -> dict[str, Any] | None:
    """Return one row dict or None if not found."""
    return db.fetch_one(
        "SELECT * FROM worker_runs WHERE worker_label = %s AND brand = %s LIMIT 1",
        (label, brand),
    )
