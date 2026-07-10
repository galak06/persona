"""Tests for `lib/schedule_db.py` (schedule_tasks table via `lib/db.py`).

Real integration tests against a live local Postgres, following
`test_db.py`'s skipif pattern — they run when one is reachable at
`DATABASE_URL` (or `lib.db_pool`'s local dev default) and skip cleanly
otherwise. CI provides a `postgres:16` service container with `DATABASE_URL`
set, so they run for real there.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from lib import db, schedule_db

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def _postgres_reachable() -> bool:
    """Best-effort connectivity probe, used to skip DB tests when none is available."""
    try:
        return db.health_check()
    except Exception:
        return False


_PG_AVAILABLE = _postgres_reachable()
_SKIP_REASON = "No reachable Postgres at DATABASE_URL (or lib.db_pool's local default)"
requires_postgres = pytest.mark.skipif(not _PG_AVAILABLE, reason=_SKIP_REASON)


@pytest.fixture
def pg() -> Iterator[None]:
    """Apply schema.sql (idempotent), yield, then truncate schedule_tasks."""
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE schedule_tasks")


def test_connect_is_a_no_op() -> None:
    """No live DB needed — connect() never touches Postgres."""
    schedule_db.connect()
    schedule_db.connect("/some/unused/path.db")


@requires_postgres
def test_save_task_then_load_all_round_trips_known_columns(pg: None) -> None:
    schedule_db.save_task(
        None,
        {
            "id": "dogfood-ig-scanner",
            "title": "IG Scanner",
            "description": "Scan hashtags",
            "order_num": 2,
            "script": "scripts/ig_scan.py",
            "args": ["--headless"],
            "depends_on": ["dogfood-site-analyzer"],
            "requires_approval": True,
            "requires_browser": True,
            "re_run_guard": False,
            "schedule": {"cron": "0 19 * * *"},
            "inputs": [],
            "telegram_notify": 1,
            "extra": {"note": "seed"},
        },
    )
    rows = schedule_db.load_all()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "dogfood-ig-scanner"
    assert row["title"] == "IG Scanner"
    assert row["args"] == ["--headless"]
    assert row["depends_on"] == ["dogfood-site-analyzer"]
    assert row["requires_approval"] == 1
    assert row["requires_browser"] == 1
    assert row["re_run_guard"] == 0
    assert row["schedule"] == {"cron": "0 19 * * *"}
    assert row["inputs"] == []
    assert row["telegram_notify"] == 1
    assert row["extra"] == {"note": "seed"}


@requires_postgres
def test_load_all_defaults_null_list_columns_to_empty_list(pg: None) -> None:
    schedule_db.save_task(None, {"id": "t1", "title": "Minimal task"})
    row = schedule_db.load_all()[0]
    assert row["args"] == []
    assert row["depends_on"] == []
    assert row["inputs"] == []


@requires_postgres
def test_load_all_orders_by_order_num_asc_then_id_asc(pg: None) -> None:
    schedule_db.save_task(None, {"id": "z-task", "order_num": 1})
    schedule_db.save_task(None, {"id": "a-task", "order_num": 1})
    schedule_db.save_task(None, {"id": "b-task", "order_num": 0})

    ids = [row["id"] for row in schedule_db.load_all()]
    assert ids == ["b-task", "a-task", "z-task"]


@requires_postgres
def test_save_task_folds_unknown_keys_into_extra(pg: None) -> None:
    schedule_db.save_task(
        None,
        {
            "id": "t1",
            "title": "Task",
            "weird_extra_key": "keepme",
            "another_key": 42,
        },
    )
    row = schedule_db.load_all()[0]
    assert row["extra"] == {"weird_extra_key": "keepme", "another_key": 42}


@requires_postgres
def test_save_task_spillover_merges_with_extra_already_in_payload(pg: None) -> None:
    schedule_db.save_task(
        None,
        {
            "id": "t1",
            "extra": {"pre_existing": "value"},
            "unknown_field": "spillover",
        },
    )
    row = schedule_db.load_all()[0]
    assert row["extra"] == {"pre_existing": "value", "unknown_field": "spillover"}


@requires_postgres
def test_save_task_upsert_updates_only_passed_columns(pg: None) -> None:
    schedule_db.save_task(
        None,
        {"id": "t1", "title": "Original title", "script": "scripts/original.py"},
    )
    schedule_db.save_task(None, {"id": "t1", "title": "Updated title"})

    rows = schedule_db.load_all()
    assert len(rows) == 1  # upsert, not a second row
    row = rows[0]
    assert row["title"] == "Updated title"
    assert row["script"] == "scripts/original.py"  # untouched by the partial upsert


@requires_postgres
def test_save_task_coerces_bool_columns_to_int(pg: None) -> None:
    schedule_db.save_task(None, {"id": "t1", "requires_approval": True, "telegram_notify": False})
    row = schedule_db.load_all()[0]
    assert row["requires_approval"] == 1
    assert row["telegram_notify"] == 0
