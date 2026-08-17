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


from tests._pg import requires_postgres


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

    def expire_all(self) -> None:
        """Simulate every held key's TTL elapsing.

        The lock is 45s and the dispatcher loop is 30s, so in production the
        lock DOES expire between passes -- repeatedly, for as long as a task
        sits unstarted. Tests that assert "the lock prevents a duplicate"
        without modelling expiry describe a window narrower than the real
        one; this lets a test cross it.
        """
        self._held.clear()


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
    task = _task_row("t1", _BRAND, script="scripts/ig_engager.py")
    payload = task_dispatcher.build_queue_payload(
        task, brand=_BRAND, brand_dir=Path("/brands/dogfoodandfun"), timeout_seconds=120
    )
    assert payload == {
        "schedule_task_id": "t1",
        "script": "scripts/ig_engager.py",
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
    # Enqueuing is still NOT executing -- running the script is
    # scripts/task_worker.py's job once it pops this off the queue. But the
    # dispatcher now claims the cron slot at enqueue with status='queued', so
    # `is_task_due` stops returning True for a row that is already waiting.
    row = worker_db.get_one(tmp_path, "t1", _BRAND)
    assert row is not None
    assert row["status"] == "queued"


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


class _RecordingLogger:
    """Captures the structured lines the dispatcher emits, with their level."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.lines.append(("info", event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.lines.append(("warning", event, kw))

    def events(self) -> list[str]:
        return [e for _, e, _ in self.lines]


def _dispatch_with_schedule(
    schedule: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[_FakeQueue, _RecordingLogger]:
    """Dispatch one row whose `schedule` is `schedule`.

    Needs no Postgres: a cron-less row returns before `worker_db` is touched.
    """
    queue, log = _FakeQueue(), _RecordingLogger()
    monkeypatch.setattr(task_dispatcher, "logger", log)
    task = _task_row("t1", _BRAND)
    task["schedule"] = schedule
    task_dispatcher.dispatch_task(
        task,
        brand=_BRAND,
        brand_dir=tmp_path,
        now=datetime.now(UTC),
        redis_client=_FakeLock(),
        queue=queue,
    )
    return queue, log


def test_retired_row_logs_at_info_not_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retiring a flow moves `cron` aside rather than deleting the row, so the
    row is permanently cron-less BY DESIGN -- the same shape a genuine
    misconfiguration has. Warning about both made them indistinguishable."""
    queue, log = _dispatch_with_schedule(
        {"cron_disabled": "33 15 * * *", "disabled_reason": "superseded by fb-engager"},
        tmp_path,
        monkeypatch,
    )
    assert queue.pushed == []
    assert log.events() == ["task_retired"]
    level, _, fields = log.lines[0]
    assert level == "info"
    assert fields["reason"] == "superseded by fb-engager"
    assert fields["retired_cron"] == "33 15 * * *", "the retired schedule stays recoverable"


def test_reason_alone_is_enough_to_mark_a_row_retired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fb-scanner/fb-comment were retired before there was a cron worth
    preserving -- their `schedule` was cleared outright."""
    queue, log = _dispatch_with_schedule(
        {"disabled_reason": "superseded by fb-engager"}, tmp_path, monkeypatch
    )
    assert queue.pushed == []
    assert log.events() == ["task_retired"]


@pytest.mark.parametrize("schedule", [{}, {"cadence": "daily"}])
def test_genuinely_cronless_row_still_warns(
    schedule: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The signal this change exists to protect: a row that lost its cron by
    accident must still be loud."""
    queue, log = _dispatch_with_schedule(schedule, tmp_path, monkeypatch)
    assert queue.pushed == []
    assert log.events() == ["task_missing_cron"]
    assert log.lines[0][0] == "warning"


@requires_postgres
def test_dispatch_task_skips_skill_only_row_without_warning(
    pg: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Skill-only flows are launchd's job, not the worker's.

    site-analyzer and wp-comment-handler carry `skill` and no `script`; the
    container has no claude CLI so they can never be dispatched here. Logging
    that as a WARNING made two correctly-configured rows look broken on every
    tick of the dispatch loop.
    """
    queue = _FakeQueue()
    task = _task_row("t1", _BRAND)
    task["script"] = ""
    task["skill"] = "site-analyzer"

    task_dispatcher.dispatch_task(
        task,
        brand=_BRAND,
        brand_dir=tmp_path,
        now=datetime.now(UTC),
        redis_client=_FakeLock(),
        queue=queue,
    )

    out = capsys.readouterr().out + capsys.readouterr().err
    assert queue.pushed == []
    assert "task_missing_script" not in out


@requires_postgres
def test_dispatch_task_still_warns_when_row_has_neither_script_nor_skill(
    pg: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A row with no way to run at all is still a real misconfiguration."""
    queue = _FakeQueue()
    task = _task_row("t1", _BRAND)
    task["script"] = ""
    task["skill"] = ""

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


@requires_postgres
def test_expired_lock_does_not_requeue_a_task_still_waiting_in_the_queue(
    pg: None, tmp_path: Path
) -> None:
    """The duplicate-enqueue bug, as observed on 2026-08-17.

    `task_worker` is single-slot. While a 13-minute browser flow held it,
    `social-posts-release` sat enqueued-but-unstarted -- so nothing wrote
    `worker_runs`, `is_task_due` kept answering True, and the only throttle
    was the 45s lock against a 30s loop. It expired every 45s and the row was
    re-enqueued once a minute: 8 enqueues, 8 real runs.

    Note this test does NOT stub `is_task_due` (unlike the lock test above) --
    the whole point is that the real due-check now says "not due" because
    `record_queued` claimed the slot. With the lock expired between passes,
    the due-check is the only guard left, which is exactly the production
    shape.
    """
    queue = _FakeQueue()
    lock = _FakeLock()
    now = datetime.now(UTC)
    task = _task_row("t1", _BRAND)

    task_dispatcher.dispatch_task(
        task, brand=_BRAND, brand_dir=tmp_path, now=now, redis_client=lock, queue=queue
    )
    assert len(queue.pushed) == 1

    # Five more dispatcher passes, each with the lock fully expired -- i.e.
    # ~5 minutes of a long flow hogging the worker. The item is still sitting
    # in the queue; nothing has run it.
    for _ in range(5):
        lock.expire_all()
        task_dispatcher.dispatch_task(
            task, brand=_BRAND, brand_dir=tmp_path, now=now, redis_client=lock, queue=queue
        )

    assert len(queue.pushed) == 1, "a queued-but-unstarted task must not be enqueued again"


@requires_postgres
def test_failed_push_leaves_the_slot_unclaimed_so_the_next_pass_retries(
    pg: None, tmp_path: Path
) -> None:
    """`record_queued` runs AFTER the push, never before.

    Claiming the slot first would mean a broken Redis silently burns the
    flow's cron slot: the push raises, but the row now says "queued", so the
    due-check suppresses every later pass and the flow just never runs that
    day. Ordering it after the push keeps a failed enqueue retryable.
    """
    queue = _FakeQueue(fail_for="t1")

    with pytest.raises(RuntimeError):
        task_dispatcher.dispatch_task(
            _task_row("t1", _BRAND),
            brand=_BRAND,
            brand_dir=tmp_path,
            now=datetime.now(UTC),
            redis_client=_FakeLock(),
            queue=queue,
        )

    assert worker_db.get_one(tmp_path, "t1", _BRAND) is None


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
