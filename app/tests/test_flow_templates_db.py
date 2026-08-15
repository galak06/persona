"""Tests for `lib/flow_templates_db.py` (flow_templates table via `lib/db.py`).

Real integration tests against a live local Postgres, following
`test_schedule_db.py`'s skipif pattern -- they run when one is reachable at
`DATABASE_URL` (or `lib.db_pool`'s local dev default) and skip cleanly
otherwise. CI provides a `postgres:16` service container with `DATABASE_URL`
set, so they run for real there.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from lib import db, flow_templates_db

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


from tests._pg import requires_postgres


@pytest.fixture
def pg() -> Iterator[None]:
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE flow_templates")


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "ig-engager",
        "platform": "instagram",
        "title": "ig-engager",
        "description": "Scan hashtags",
        "order_num": 30,
        "script": "scripts/ig_engager.py",
        "skill": "ig-engager",
        "args": [],
        "depends_on": ["site-analyzer"],
        "requires_approval": False,
        "approval_channel": None,
        "requires_browser": True,
        "re_run_guard": True,
        "output_file": "state/instagram_comment_queue.json",
        "schedule": {"cron": "3 19 * * *", "cadence": "daily"},
        "inputs": [],
        "telegram_notify": True,
    }
    base.update(overrides)
    return base


@requires_postgres
def test_save_then_get_round_trips_all_columns(pg: None) -> None:
    flow_templates_db.save(_row())

    row = flow_templates_db.get("ig-engager")
    assert row is not None
    assert row["platform"] == "instagram"
    assert row["script"] == "scripts/ig_engager.py"
    assert row["depends_on"] == ["site-analyzer"]
    assert row["schedule"] == {"cron": "3 19 * * *", "cadence": "daily"}
    assert row["requires_browser"] is True
    assert row["requires_approval"] is False


@requires_postgres
def test_get_returns_none_for_unknown_id(pg: None) -> None:
    assert flow_templates_db.get("does-not-exist") is None


@requires_postgres
def test_save_upsert_updates_existing_row_not_a_duplicate(pg: None) -> None:
    flow_templates_db.save(_row())
    flow_templates_db.save(_row(description="updated description"))

    all_rows = flow_templates_db.load_all()
    assert len(all_rows) == 1
    assert all_rows[0]["description"] == "updated description"


@requires_postgres
def test_save_coerces_bool_columns_to_int_in_storage(pg: None) -> None:
    flow_templates_db.save(_row(requires_approval=True, telegram_notify=False))

    raw = db.fetch_all("SELECT requires_approval, telegram_notify FROM flow_templates")[0]
    assert raw["requires_approval"] == 1
    assert raw["telegram_notify"] == 0
