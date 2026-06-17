"""PostgreSQL-backed deduplication for DogFoodAndFun workers.

Replaces lib/deduplication.py for the two hard rules:
  - Never like the same post twice (permanent)
  - Never comment on the same post twice (permanent)

The completed_tasks table is the single source of truth.
A duplicate INSERT raises UniqueViolation which callers catch to skip.

Usage::

    from lib.dedup_pg import already_done, record_done, TaskType

    if already_done("like", "facebook", post_id):
        continue

    # ... perform the action ...

    record_done("like", "facebook", post_id, worker_label="fb-scanner")
"""

from __future__ import annotations

from typing import Literal

import psycopg2

from lib.pg_db import db_cursor

TaskType = Literal["like", "comment", "follow", "publish", "scan"]
Platform = Literal["facebook", "instagram", "wordpress"]

_BRAND = "dogfoodandfun"


def already_done(
    task_type: TaskType,
    platform: Platform,
    entity_id: str,
    brand: str = _BRAND,
) -> bool:
    """Returns True if this exact action was already recorded."""
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM completed_tasks
            WHERE task_type = %s AND platform = %s
              AND entity_id = %s AND brand = %s
            LIMIT 1
            """,
            (task_type, platform, entity_id, brand),
        )
        return cur.fetchone() is not None


def record_done(
    task_type: TaskType,
    platform: Platform,
    entity_id: str,
    brand: str = _BRAND,
    worker_label: str = "",
    meta: dict | None = None,
) -> bool:
    """Insert a completed task record.

    Returns True if inserted, False if it was already recorded (duplicate).
    Never raises on duplicate — callers can safely call this without pre-checking.
    """
    import json as _json
    try:
        with db_cursor() as cur:
            cur.execute(
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
                    _json.dumps(meta or {}),
                ),
            )
        return True
    except psycopg2.errors.UniqueViolation:
        return False


def stats(brand: str = _BRAND) -> dict[str, int]:
    """Return count per (task_type, platform) pair."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute(
            """
            SELECT task_type, platform, COUNT(*) AS cnt
            FROM completed_tasks
            WHERE brand = %s
            GROUP BY task_type, platform
            ORDER BY task_type, platform
            """,
            (brand,),
        )
        return {f"{r['task_type']}:{r['platform']}": r["cnt"] for r in cur.fetchall()}
