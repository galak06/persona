"""Tests for `scripts/task_dispatcher.py` (pure PRODUCER, PR7 split).

The pure due-check tests need no infra. The dispatch-level tests are real
integration tests against a live local Postgres, following the project's
`requires_postgres` skipif convention (see `tests/test_db.py`) -- they run
when one is reachable at `DATABASE_URL` and skip cleanly otherwise; CI's
`postgres:16` service container makes them run for real there.

The Redis lock is exercised via `_FakeLock`, a tiny in-memory stand-in that
mirrors `redis.Redis.set`'s NX/EX contract (`True` on success, `None` when
`nx=True` blocks) -- no live Redis server is needed for these tests. The
`flow-run` queue is exercised via `_FakeQueue`, a tiny in-memory stand-in
for `lib.task_queue.TaskQueue.push()` -- no live Redis queue is needed
either. Neither fake means the dispatcher runs anything: since PR7, it only
enqueues -- `scripts/task_worker.py` (tested separately, in
`tests/test_task_worker.py`) is what actually executes.
"""

from __future__ import annotations

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


class _FakeQueue:
    """In-memory stand-in for `lib.task_queue.TaskQueue.push()`.

    `fail_for` optionally names a `schedule_task_id` whose push raises --
    used to exercise `run_once`'s per-row exception handling without a real
    dependency failure.
    """

    def __init__(self, *, fail_for: str | None = None) -> None:
        self.pushed: list[dict[str, Any]] = []
        self._fail_for = fail_for

    def push(self, payload: dict[str, Any]) -> str:
        if self._fail_for and payload.get("schedule_task_id") == self._fail_for:
            raise RuntimeError("queue unavailable")
        self.pushed.append(payload)
        return "fake-queue-item-id"


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


# --------------------------------------------------------------------- build_queue_payload (pure, no infra)


def test_build_queue_payload_shape() -> None:
    task = _task_row("t1", _BRAND, script="scripts/ig_scan.py")
    payload = task_dispatcher.build_queue_payload(
        task, brand=_BRAND, brand_dir=Path("/brands/dogfoodandfun"), timeout_seconds=120
    )
    assert payload == {
        "schedule_task_id": "t1",
        "script": "scripts/ig_scan.py",
        "args": [],
        "brand": _BRAND,
        "brand_dir": "/brands/dogfoodandfun",
        "timeout_seconds": 120,
    }


# --------------------------------------------------------------------------- dispatch_task


@requires_postgres
def test_dispatch_task_enqueues_due_task_without_executing_it(pg: None, tmp_path: Path) -> None:
    queue = _FakeQueue()

    task_dispatcher.dispatch_task(
        _task_row("t1", _BRAND),
        brand=_BRAND,
        brand_dir=tmp_path,
        now=datetime.now(UTC),
        redis_client=_FakeLock(),
        queue=queue,
    )

    assert len(queue.pushed) == 1
    assert queue.pushed[0]["schedule_task_id"] == "t1"
    # Enqueuing is NOT executing -- worker_runs stays untouched; that's
    # scripts/task_worker.py's job once it pops this off the queue.
    assert worker_db.get_one(tmp_path, "t1", _BRAND) is None


@requires_postgres
def test_dispatch_task_skips_when_not_due(pg: None, tmp_path: Path) -> None:
    queue = _FakeQueue()
    now = datetime.now(UTC)
    worker_db.record_complete(tmp_path, "t1", _BRAND, "success")  # last_run = now

    task_dispatcher.dispatch_task(
        _task_row("t1", _BRAND),
        brand=_BRAND,
        brand_dir=tmp_path,
        now=now,
        redis_client=_FakeLock(),
        queue=queue,
    )

    assert queue.pushed == []


@requires_postgres
def test_dispatch_task_skips_row_missing_cron(pg: None, tmp_path: Path) -> None:
    queue = _FakeQueue()
    task = _task_row("t1", _BRAND)
    task["schedule"] = {}

    task_dispatcher.dispatch_task(
        task,
        brand=_BRAND,
        brand_dir=tmp_path,
        now=datetime.now(UTC),
        redis_client=_FakeLock(),
        queue=queue,
    )

    assert queue.pushed == []


@requires_postgres
def test_dispatch_task_lock_prevents_double_enqueue(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Isolates the Redis lock from the due-check: `is_task_due` is forced
    True on both calls (a real second call would also see "not due" once
    `record_complete` lands, which is a *different* guard tested above)."""
    monkeypatch.setattr(task_dispatcher, "is_task_due", lambda *a, **k: True)

    queue = _FakeQueue()
    lock = _FakeLock()
    now = datetime.now(UTC)
    task = _task_row("t1", _BRAND)

    task_dispatcher.dispatch_task(
        task, brand=_BRAND, brand_dir=tmp_path, now=now, redis_client=lock, queue=queue
    )
    task_dispatcher.dispatch_task(
        task, brand=_BRAND, brand_dir=tmp_path, now=now, redis_client=lock, queue=queue
    )

    assert len(queue.pushed) == 1  # second call saw the held lock and skipped


# --------------------------------------------------------------------------- run_once


@requires_postgres
def test_run_once_only_enqueues_rows_for_its_own_brand(pg: None, tmp_path: Path) -> None:
    queue = _FakeQueue()
    schedule_db.save_task(None, _task_row("mine", _BRAND))
    schedule_db.save_task(None, _task_row("theirs", _OTHER_BRAND))

    task_dispatcher.run_once(
        brand=_BRAND,
        brand_dir=tmp_path,
        now=datetime.now(UTC),
        redis_client=_FakeLock(),
        queue=queue,
    )

    assert [p["schedule_task_id"] for p in queue.pushed] == ["mine"]


@requires_postgres
def test_run_once_continues_after_one_task_fails_to_enqueue(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notified: list[tuple[str, str]] = []
    monkeypatch.setattr(
        task_dispatcher,
        "_notify_telegram_failure",
        lambda task_id, error: notified.append((task_id, error)),
    )

    queue = _FakeQueue(fail_for="bad-task")
    schedule_db.save_task(None, _task_row("bad-task", _BRAND, order_num=0))
    schedule_db.save_task(None, _task_row("good-task", _BRAND, order_num=1))

    task_dispatcher.run_once(
        brand=_BRAND,
        brand_dir=tmp_path,
        now=datetime.now(UTC),
        redis_client=_FakeLock(),
        queue=queue,
    )

    assert [p["schedule_task_id"] for p in queue.pushed] == ["good-task"]
    assert notified and notified[0][0] == "bad-task"
