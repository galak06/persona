"""Supabase-backed helper for recording worker run status.

Stores one row per (worker_label, brand) pair. The brand_dir parameter is still
accepted for backward compat (used only for PID file cleanup in record_complete).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


def record_start(brand_dir: str | Path, label: str, brand: str) -> None:
    """Upsert status='running', last_run=now UTC."""
    from lib.supabase_client import get_client

    client = get_client()
    client.table("worker_runs").upsert(
        {"worker_label": label, "brand": brand, "status": "running", "last_run": _now_utc(), "message": ""},
        on_conflict="worker_label,brand",
    ).execute()


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
    from lib.supabase_client import get_client

    client = get_client()
    client.table("worker_runs").upsert(
        {"worker_label": label, "brand": brand, "status": status, "last_run": _now_utc(), "message": message},
        on_conflict="worker_label,brand",
    ).execute()

    logs_dir = Path(brand_dir) / "logs"
    suffix = label.removeprefix("com.dogfoodandfun.").replace("-", "_")
    for pattern in (f"{suffix}.pid", f"{suffix}_0.pid", f"{suffix}_1.pid", f"{suffix}_2.pid"):
        p = logs_dir / pattern
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def get_all(brand_dir: str | Path, brand: str) -> list[dict]:
    """Return all rows for this brand as a list of dicts."""
    from lib.supabase_client import get_client

    client = get_client()
    result = (
        client.table("worker_runs")
        .select("*")
        .eq("brand", brand)
        .order("last_run", desc=True)
        .execute()
    )
    return result.data or []


def get_one(brand_dir: str | Path, label: str, brand: str) -> dict | None:
    """Return one row dict or None if not found."""
    from lib.supabase_client import get_client

    client = get_client()
    result = (
        client.table("worker_runs")
        .select("*")
        .eq("worker_label", label)
        .eq("brand", brand)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None
