"""Tests for `scripts/task_dispatcher.py` (Phase A Postgres+Redis dispatcher).

The pure due-check tests need no infra. The dispatch-level tests are real
integration tests against a live local Postgres, following the project's
`requires_postgres` skipif convention (see `tests/test_db.py`) -- they run
when one is reachable at `DATABASE_URL` and skip cleanly otherwise; CI's
`postgres:16` service container makes them run for real there.

The Redis lock is exercised via `_FakeLock`, a tiny in-memory stand-in that
mirrors `redis.Redis.set`'s NX/EX contract (`True` on success, `None` when
`nx=True` blocks) -- no live Redis server is needed for these tests. The
real `persona:{brand}:dispatch:{task_id}` key against a live Redis is
exercised by the plan's manual verification steps, not here.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import task_dispatcher

from lib import db, schedule_db, worker_db
from lib.scheduling import is_task_due

_SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"
_BRAND = "dogfoodandfun"
_OTHER_BRAND = "otherbrand"


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
    """Apply schema.sql (idempotent), yield, then truncate the tables this module touched."""
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE schedule_tasks, worker_runs")


class _FakeLock:
    """In-memory stand-in for `redis.Redis.set`'s SET NX EX contract.

    No expiry simulation -- tests run in milliseconds, well inside any real
    TTL, so "acquired once, held for the rest of the test" is all that's
    needed to exercise the lock-prevents-double-dispatch behaviour.
    """

    def __init__(self) -> None:
        self._held: set[str] = set()

    def set(self, name: str, value: str, *, nx: bool = False, ex: int | None = None) -> Any:
        if nx and name in self._held:
            return None
        self._held.add(name)
        return True


def _task_row(
    task_id: str,
    brand_id: str,
    *,
    script: str = "scripts/noop_healthcheck.py",
    order_num: int = 0,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "brand_id": brand_id,
        "script": script,
        "schedule": {"cron": "* * * * *"},
        "args": [],
        "order_num": order_num,
    }


def _fake_run(returncode: int = 0) -> Any:
    """Build a fake `subprocess.run` replacement recording every call."""
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="boom")

    _run.calls = calls  # type: ignore[attr-defined]
    return _run


# --------------------------------------------------------------------------- is_task_due (pure, no infra)


def test_is_task_due_true_when_never_run() -> None:
    assert is_task_due("* * * * *", None, datetime.now(UTC)) is True


def test_is_task_due_false_immediately_after_a_run() -> None:
    last_run = datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC).isoformat()
    now = datetime(2026, 7, 9, 12, 0, 30, tzinfo=UTC)
    assert is_task_due("* * * * *", last_run, now) is False


def test_is_task_due_true_once_the_next_minute_arrives() -> None:
    last_run = datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC).isoformat()
    now = datetime(2026, 7, 9, 12, 1, 0, tzinfo=UTC)
    assert is_task_due("* * * * *", last_run, now) is True


def test_is_task_due_false_for_malformed_cron_with_a_prior_run() -> None:
    last_run = datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC).isoformat()
    assert is_task_due("not a cron", last_run, datetime.now(UTC)) is False


def test_is_task_due_true_for_malformed_last_run_timestamp() -> None:
    assert is_task_due("* * * * *", "not-a-timestamp", datetime.now(UTC)) is True


# --------------------------------------------------------------------------- dispatch_task


@requires_postgres
def test_dispatch_task_runs_due_task_and_records_success(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_run = _fake_run(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)

    task_dispatcher.dispatch_task(
        _task_row("t1", _BRAND),
        brand=_BRAND,
        brand_dir=tmp_path,
        now=datetime.now(UTC),
        redis_client=_FakeLock(),
    )

    assert len(fake_run.calls) == 1
    row = worker_db.get_one(tmp_path, "t1", _BRAND)
    assert row is not None
    assert row["status"] == "success"


@requires_postgres
def test_dispatch_task_skips_when_not_due(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_run = _fake_run(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)

    now = datetime.now(UTC)
    worker_db.record_complete(tmp_path, "t1", _BRAND, "success")  # last_run = now

    task_dispatcher.dispatch_task(
        _task_row("t1", _BRAND),
        brand=_BRAND,
        brand_dir=tmp_path,
        now=now,
        redis_client=_FakeLock(),
    )

    assert fake_run.calls == []


@requires_postgres
def test_dispatch_task_skips_row_missing_cron(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_run = _fake_run(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)

    task = _task_row("t1", _BRAND)
    task["schedule"] = {}

    task_dispatcher.dispatch_task(
        task, brand=_BRAND, brand_dir=tmp_path, now=datetime.now(UTC), redis_client=_FakeLock()
    )

    assert fake_run.calls == []


@requires_postgres
def test_dispatch_task_lock_prevents_double_dispatch(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Isolates the Redis lock from the due-check: `is_task_due` is forced
    True on both calls (a real second call would also see "not due" once
    `record_complete` lands, which is a *different* guard tested above)."""
    fake_run = _fake_run(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(task_dispatcher, "is_task_due", lambda *a, **k: True)

    lock = _FakeLock()
    now = datetime.now(UTC)
    task = _task_row("t1", _BRAND)

    task_dispatcher.dispatch_task(
        task, brand=_BRAND, brand_dir=tmp_path, now=now, redis_client=lock
    )
    task_dispatcher.dispatch_task(
        task, brand=_BRAND, brand_dir=tmp_path, now=now, redis_client=lock
    )

    assert len(fake_run.calls) == 1  # second call saw the held lock and skipped


# --------------------------------------------------------------------------- run_once


@requires_postgres
def test_run_once_only_dispatches_rows_for_its_own_brand(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_run = _fake_run(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)

    schedule_db.save_task(None, _task_row("mine", _BRAND))
    schedule_db.save_task(None, _task_row("theirs", _OTHER_BRAND))

    task_dispatcher.run_once(
        brand=_BRAND, brand_dir=tmp_path, now=datetime.now(UTC), redis_client=_FakeLock()
    )

    assert len(fake_run.calls) == 1
    assert worker_db.get_one(tmp_path, "mine", _BRAND) is not None
    assert worker_db.get_one(tmp_path, "theirs", _OTHER_BRAND) is None


@requires_postgres
def test_run_once_continues_after_one_task_fails(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    notified: list[tuple[str, str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd[1])
        if "bad" in cmd[1]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        task_dispatcher,
        "_notify_telegram_failure",
        lambda task_id, error: notified.append((task_id, error)),
    )

    schedule_db.save_task(None, _task_row("bad-task", _BRAND, script="scripts/bad.py", order_num=0))
    schedule_db.save_task(
        None, _task_row("good-task", _BRAND, script="scripts/good.py", order_num=1)
    )

    task_dispatcher.run_once(
        brand=_BRAND, brand_dir=tmp_path, now=datetime.now(UTC), redis_client=_FakeLock()
    )

    assert len(calls) == 2  # both attempted despite the first failing
    bad_row = worker_db.get_one(tmp_path, "bad-task", _BRAND)
    good_row = worker_db.get_one(tmp_path, "good-task", _BRAND)
    assert bad_row is not None
    assert bad_row["status"] == "error"
    assert good_row is not None
    assert good_row["status"] == "success"
    assert notified and notified[0][0] == "bad-task"
