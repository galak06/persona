"""Tests for `lib/worker_db.py` (worker_runs table via `lib/db.py`).

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

from lib import db, worker_db

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
    """Apply schema.sql (idempotent), yield, then truncate worker_runs."""
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE worker_runs")


@requires_postgres
def test_record_start_inserts_running_row(pg: None, tmp_path: Path) -> None:
    worker_db.record_start(tmp_path, "com.persona.ig-scanner", "dogfoodandfun")
    row = worker_db.get_one(tmp_path, "com.persona.ig-scanner", "dogfoodandfun")
    assert row is not None
    assert row["status"] == "running"
    assert row["message"] == ""
    assert row["last_run"]


@requires_postgres
def test_record_start_then_complete_upserts_same_row_keyed_by_label_and_brand(
    pg: None, tmp_path: Path
) -> None:
    worker_db.record_start(tmp_path, "com.persona.fb-scanner", "dogfoodandfun")
    worker_db.record_complete(
        tmp_path, "com.persona.fb-scanner", "dogfoodandfun", "success", "done"
    )

    rows = db.fetch_all("SELECT * FROM worker_runs")
    assert len(rows) == 1  # upsert, not a second row
    row = rows[0]
    assert row["worker_label"] == "com.persona.fb-scanner"
    assert row["brand"] == "dogfoodandfun"
    assert row["status"] == "success"
    assert row["message"] == "done"


@requires_postgres
def test_record_complete_updates_last_run(pg: None, tmp_path: Path) -> None:
    worker_db.record_start(tmp_path, "com.persona.wp-comments", "dogfoodandfun")
    first = worker_db.get_one(tmp_path, "com.persona.wp-comments", "dogfoodandfun")
    assert first is not None
    first_last_run = first["last_run"]

    worker_db.record_complete(tmp_path, "com.persona.wp-comments", "dogfoodandfun", "error", "boom")
    second = worker_db.get_one(tmp_path, "com.persona.wp-comments", "dogfoodandfun")
    assert second is not None
    assert second["status"] == "error"
    assert second["message"] == "boom"
    assert second["last_run"] >= first_last_run


@requires_postgres
def test_get_all_scopes_by_brand_and_orders_last_run_desc(pg: None, tmp_path: Path) -> None:
    worker_db.record_start(tmp_path, "com.persona.a", "dogfoodandfun")
    worker_db.record_complete(tmp_path, "com.persona.a", "dogfoodandfun", "success")
    worker_db.record_start(tmp_path, "com.persona.b", "dogfoodandfun")
    worker_db.record_complete(tmp_path, "com.persona.b", "dogfoodandfun", "success")
    worker_db.record_start(tmp_path, "com.persona.other-brand", "another-brand")

    rows = worker_db.get_all(tmp_path, "dogfoodandfun")
    assert len(rows) == 2
    assert {r["worker_label"] for r in rows} == {"com.persona.a", "com.persona.b"}
    # ordered by last_run DESC — most recent first
    assert rows[0]["last_run"] >= rows[1]["last_run"]


@requires_postgres
def test_get_one_returns_none_when_missing(pg: None, tmp_path: Path) -> None:
    assert worker_db.get_one(tmp_path, "com.persona.nope", "dogfoodandfun") is None


@requires_postgres
def test_record_complete_removes_stale_pid_files(pg: None, tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)
    label = "com.persona.ig-scanner"
    suffix = label.removeprefix("com.persona.").replace("-", "_")
    pid_files = [
        logs_dir / f"{suffix}.pid",
        logs_dir / f"{suffix}_0.pid",
        logs_dir / f"{suffix}_1.pid",
        logs_dir / f"{suffix}_2.pid",
    ]
    for p in pid_files:
        p.write_text("12345")
    unrelated = logs_dir / "other_worker.pid"
    unrelated.write_text("999")

    worker_db.record_complete(tmp_path, label, "dogfoodandfun", "success")

    for p in pid_files:
        assert not p.exists()
    assert unrelated.exists()  # not touched


@requires_postgres
def test_record_complete_tolerates_missing_pid_files(pg: None, tmp_path: Path) -> None:
    """No logs dir at all — should not raise."""
    worker_db.record_complete(tmp_path, "com.persona.never-started", "dogfoodandfun", "success")
    row = worker_db.get_one(tmp_path, "com.persona.never-started", "dogfoodandfun")
    assert row is not None
    assert row["status"] == "success"
