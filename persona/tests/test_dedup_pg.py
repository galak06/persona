"""Tests for `lib/dedup_pg.py` (completed_tasks table via `lib/db.py`).

Real integration tests against a live local Postgres, following
`test_worker_db.py`'s skipif pattern.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from lib import db, dedup_pg

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def _postgres_reachable() -> bool:
    try:
        return db.health_check()
    except Exception:
        return False


_PG_AVAILABLE = _postgres_reachable()
_SKIP_REASON = "No reachable Postgres at DATABASE_URL (or lib.db_pool's local default)"
requires_postgres = pytest.mark.skipif(not _PG_AVAILABLE, reason=_SKIP_REASON)


@pytest.fixture
def pg() -> Iterator[None]:
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE completed_tasks")


@requires_postgres
def test_already_done_false_when_never_recorded(pg: None) -> None:
    assert dedup_pg.already_done("like", "instagram", "post-1") is False


@requires_postgres
def test_record_done_then_already_done_true(pg: None) -> None:
    assert dedup_pg.record_done("like", "instagram", "post-1", brand="dogfoodandfun") is True
    assert dedup_pg.already_done("like", "instagram", "post-1", brand="dogfoodandfun") is True


@requires_postgres
def test_record_done_returns_false_on_duplicate(pg: None) -> None:
    assert dedup_pg.record_done("comment", "facebook", "post-2") is True
    assert dedup_pg.record_done("comment", "facebook", "post-2") is False


@requires_postgres
def test_record_done_scopes_by_brand(pg: None) -> None:
    dedup_pg.record_done("like", "instagram", "post-3", brand="brand-a")
    assert dedup_pg.already_done("like", "instagram", "post-3", brand="brand-a") is True
    assert dedup_pg.already_done("like", "instagram", "post-3", brand="brand-b") is False


@requires_postgres
def test_stats_counts_per_task_type_and_platform(pg: None) -> None:
    dedup_pg.record_done("like", "instagram", "post-4", brand="dogfoodandfun")
    dedup_pg.record_done("like", "instagram", "post-5", brand="dogfoodandfun")
    dedup_pg.record_done("comment", "facebook", "post-6", brand="dogfoodandfun")

    assert dedup_pg.stats(brand="dogfoodandfun") == {
        "like:instagram": 2,
        "comment:facebook": 1,
    }
