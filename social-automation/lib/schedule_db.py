"""Supabase-backed CRUD helpers for the schedule_tasks table.

JSON/JSONB columns are auto-parsed by the Supabase client on read.
The public API (connect, load_all, save_task) is preserved for backward compat.
"""

from __future__ import annotations

import logging

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
}


def connect(db_path: str | None = None) -> None:
    """No-op: schedule DB now lives in Supabase."""
    return None


def load_all(conn: object | None = None) -> list[dict]:
    """Return all schedule_tasks ordered by order_num ASC, id ASC."""
    from lib.supabase_client import get_client

    client = get_client()
    result = (
        client.table("schedule_tasks")
        .select("*")
        .order("order_num", desc=False)
        .order("id", desc=False)
        .execute()
    )
    rows: list[dict] = result.data or []

    out: list[dict] = []
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


def save_task(conn: object | None, task: dict) -> None:
    """Upsert one task. Unknown keys are folded into the ``extra`` JSON column."""
    from lib.supabase_client import get_client

    known: dict = {}
    spillover: dict = {}

    for key, value in task.items():
        if key in _KNOWN_COLUMNS:
            known[key] = value
        else:
            spillover[key] = value

    if spillover:
        existing_extra = known.get("extra") or {}
        if isinstance(existing_extra, str):
            import json
            existing_extra = json.loads(existing_extra)
        existing_extra.update(spillover)
        known["extra"] = existing_extra

    for col in _BOOL_COLUMNS:
        if col in known and known[col] is not None:
            known[col] = int(bool(known[col]))

    client = get_client()
    client.table("schedule_tasks").upsert(known).execute()
