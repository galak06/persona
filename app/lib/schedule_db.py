"""Postgres-backed CRUD helpers for the schedule_tasks table (via `lib/db.py`).

JSON/JSONB columns are auto-parsed to Python objects on read by psycopg;
writes wrap dict/list values in `Jsonb(...)` so psycopg serializes them
correctly. The public API (connect, load_all, save_task) is preserved for
backward compat.

`is_retired()` lives here rather than in either consumer because "is this row
retired" must have exactly ONE definition: `scripts/task_dispatcher.py`
refusing to run a retired row while `lib/flow_readiness.py` happily read one's
frozen `worker_runs` status is how a retired 2026-08-31 migration row shadowed
the live one on the Flow status panel for a week.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from psycopg.types.json import Jsonb

from lib import db

logger = logging.getLogger(__name__)

_JSON_COLUMNS = {"args", "depends_on", "schedule", "inputs", "extra"}
_LIST_COLUMNS = {"args", "depends_on", "inputs"}
_BOOL_COLUMNS = {
    "requires_approval",
    "requires_browser",
    "re_run_guard",
    "telegram_notify",
}
_KNOWN_COLUMNS = {
    "id",
    "title",
    "description",
    "order_num",
    "script",
    "skill",
    "args",
    "timeout_minutes",
    "depends_on",
    "requires_approval",
    "requires_browser",
    "re_run_guard",
    "output_file",
    "schedule",
    "inputs",
    "telegram_notify",
    "extra",
    "brand_id",
}


# A flow is retired by moving `schedule.cron` aside instead of deleting the row,
# so the schedule it used to run on stays recoverable. Either key marks the row
# as deliberately cron-less; `disabled_reason` alone is enough for rows retired
# before there was a cron worth preserving.
RETIRED_CRON_KEY = "cron_disabled"
RETIRED_REASON_KEY = "disabled_reason"


def is_retired(task: dict[str, Any]) -> bool:
    """Whether `task` (a `schedule_tasks` row) was retired rather than deleted.

    Retiring is additive -- the 2026-08-31 task-id migration
    (`scripts/migrate_task_ids_to_brand.py`) left every superseded row in
    place, cron moved to `cron_disabled` and `disabled_reason` recorded. Such
    a row must never be dispatched, and must never be mistaken for a flow's
    live row: its `worker_runs` status is frozen at whatever it held the day
    it was retired.
    """
    schedule = task.get("schedule") or {}
    return RETIRED_CRON_KEY in schedule or RETIRED_REASON_KEY in schedule


def connect(db_path: str | None = None) -> None:
    """No-op: schedule DB now lives in Postgres (see lib/db.py)."""
    return None


def load_all(conn: object | None = None) -> list[dict[str, Any]]:
    """Return all schedule_tasks ordered by order_num ASC, id ASC."""
    rows = db.fetch_all("SELECT * FROM schedule_tasks ORDER BY order_num ASC, id ASC")

    out: list[dict[str, Any]] = []
    for row in rows:
        task = dict(row)
        for col in _LIST_COLUMNS:
            if task.get(col) is None:
                task[col] = []
        for col in _BOOL_COLUMNS:
            if col in task and task[col] is not None:
                task[col] = int(bool(task[col]))
        out.append(task)
    return out


def save_task(conn: object | None, task: dict[str, Any]) -> None:
    """Upsert one task. Unknown keys are folded into the ``extra`` JSON column."""
    known: dict[str, Any] = {}
    spillover: dict[str, Any] = {}

    for key, value in task.items():
        if key in _KNOWN_COLUMNS:
            known[key] = value
        else:
            spillover[key] = value

    if spillover:
        existing_extra = known.get("extra") or {}
        if isinstance(existing_extra, str):
            existing_extra = json.loads(existing_extra)
        existing_extra.update(spillover)
        known["extra"] = existing_extra

    for col in _BOOL_COLUMNS:
        if col in known and known[col] is not None:
            known[col] = int(bool(known[col]))

    columns = list(known.keys())
    params = {
        col: Jsonb(known[col]) if col in _JSON_COLUMNS and known[col] is not None else known[col]
        for col in columns
    }

    col_list = ", ".join(columns)
    placeholders = ", ".join(f"%({col})s" for col in columns)
    update_cols = [col for col in columns if col != "id"]
    conflict_clause = (
        ", ".join(f"{col} = EXCLUDED.{col}" for col in update_cols) if update_cols else None
    )

    query = f"""
        INSERT INTO schedule_tasks ({col_list})
        VALUES ({placeholders})
        ON CONFLICT (id) DO {"UPDATE SET " + conflict_clause if conflict_clause else "NOTHING"}
    """
    db.execute(query, params)
