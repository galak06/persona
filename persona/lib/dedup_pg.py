"""PostgreSQL-backed deduplication for Persona workers.

Replaces lib/deduplication.py for the two hard rules:
  - Never like the same post twice (permanent)
  - Never comment on the same post twice (permanent)

The completed_tasks table (db/schema.sql) is the single source of truth.
Built on lib.db's shared psycopg3 pool (DATABASE_URL) -- the same
connection every other Postgres-backed module in this app already uses,
not a separate psycopg2 pool/DSN.

Usage::

    from lib.dedup_pg import already_done, record_done, TaskType

    if already_done("like", "facebook", post_id):
        continue

    # ... perform the action ...

    record_done("like", "facebook", post_id, worker_label="fb-scanner")
"""

from __future__ import annotations

import json
from typing import Any, Literal

import psycopg

from lib.db import execute, fetch_all, fetch_one

TaskType = Literal["like", "comment", "follow", "publish", "scan"]
Platform = Literal["facebook", "instagram", "wordpress"]

_BRAND = "persona"


def already_done(
    task_type: TaskType,
    platform: Platform,
    entity_id: str,
    brand: str = _BRAND,
) -> bool:
    """Returns True if this exact action was already recorded."""
    row = fetch_one(
        """
        SELECT 1 FROM completed_tasks
        WHERE task_type = %s AND platform = %s
          AND entity_id = %s AND brand = %s
        LIMIT 1
        """,
        (task_type, platform, entity_id, brand),
    )
    return row is not None


def completed_entity_ids(
    task_type: TaskType,
    platform: Platform,
    brand: str = _BRAND,
) -> set[str]:
    """Every entity_id already recorded for this (task_type, platform, brand).

    The bulk counterpart to `already_done`, for callers that would otherwise
    issue one SELECT per candidate. A scan checks ~570 posts per run inside a
    live browser session; prefetching the seen set once turns those sequential
    round-trips into a single indexed read (idx_completed_tasks_brand covers
    exactly this key order).
    """
    rows = fetch_all(
        """
        SELECT entity_id FROM completed_tasks
        WHERE task_type = %s AND platform = %s AND brand = %s
        """,
        (task_type, platform, brand),
    )
    return {r["entity_id"] for r in rows}


def record_done(
    task_type: TaskType,
    platform: Platform,
    entity_id: str,
    brand: str = _BRAND,
    worker_label: str = "",
    meta: dict[str, Any] | None = None,
) -> bool:
    """Insert a completed task record.

    Returns True if inserted, False if it was already recorded (duplicate).
    Never raises on duplicate — callers can safely call this without pre-checking.
    """
    try:
        execute(
            """
            INSERT INTO completed_tasks
                (task_type, platform, entity_id, brand, worker_label, meta)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                task_type,
                platform,
                entity_id,
                brand,
                worker_label,
                json.dumps(meta or {}),
            ),
        )
        return True
    except psycopg.errors.UniqueViolation:
        return False


def stats(brand: str = _BRAND) -> dict[str, int]:
    """Return count per (task_type, platform) pair."""
    rows = fetch_all(
        """
        SELECT task_type, platform, COUNT(*) AS cnt
        FROM completed_tasks
        WHERE brand = %s
        GROUP BY task_type, platform
        ORDER BY task_type, platform
        """,
        (brand,),
    )
    return {f"{r['task_type']}:{r['platform']}": r["cnt"] for r in rows}
