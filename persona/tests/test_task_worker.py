"""Tests for `scripts/task_worker.py` (pure CONSUMER, PR7 split).

`run_task`/`_process_one` tests are real integration tests against a live
local Postgres (worker_runs writes), following the project's
`requires_postgres` skipif convention -- run for real in CI's ephemeral
`postgres:16` service, skip cleanly otherwise. `drain_once` is exercised
against `_FakeConsumerQueue`, an in-memory stand-in for
`lib.task_queue.TaskQueue`'s consumer half (`pop`/`pop_nowait`) -- no live
Redis needed.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import task_worker

from lib import db, worker_db

_BRAND = "dogfoodandfun"


def _postgres_reachable() -> bool:
    try:
        return db.health_check()
    except Exception:
        return False


_PG_AVAILABLE = _postgres_reachable()
requires_postgres = pytest.mark.skipif(
    not _PG_AVAILABLE, reason="No reachable Postgres at DATABASE_URL"
)


@pytest.fixture
def pg() -> Iterator[None]:
    schema_path = PROJECT_ROOT / "db" / "schema.sql"
    db.execute(schema_path.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE worker_runs")


class _FakeConsumerQueue:
    """In-memory stand-in for `TaskQueue`'s consumer half."""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = list(items)

    def pop_nowait(self) -> dict[str, Any] | None:
        return self._items.pop(0) if self._items else None

    def pop(self, timeout: int = 30) -> dict[str, Any] | None:
        return self.pop_nowait()


def _queue_item(
    schedule_task_id: str, brand_dir: Path, *, script: str = "scripts/noop_healthcheck.py"
) -> dict[str, Any]:
    return {
        "schedule_task_id": schedule_task_id,
        "script": script,
        "args": [],
        "brand": _BRAND,
        "brand_dir": str(brand_dir),
        "timeout_seconds": 60,
    }


def _fake_run(returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    _run.calls = calls  # type: ignore[attr-defined]
    return _run


# --------------------------------------------------------------------------- run_task


@requires_postgres
def test_run_task_executes_and_records_success(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_run = _fake_run(returncode=0, stdout="all good")
    monkeypatch.setattr(subprocess, "run", fake_run)

    task_worker.run_task(_queue_item("t1", tmp_path))

    assert len(fake_run.calls) == 1
    row = worker_db.get_one(tmp_path, "t1", _BRAND)
    assert row is not None
    assert row["status"] == "success"
    assert row["message"] == "all good"


@requires_postgres
def test_run_task_records_error_on_nonzero_exit(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_run = _fake_run(returncode=1, stderr="boom")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError):
        task_worker.run_task(_queue_item("t1", tmp_path))

    row = worker_db.get_one(tmp_path, "t1", _BRAND)
    assert row is not None
    assert row["status"] == "error"
    assert "boom" in row["message"]


@requires_postgres
def test_run_task_records_error_on_launch_failure(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("no such file")

    monkeypatch.setattr(subprocess, "run", _boom)

    with pytest.raises(OSError):
        task_worker.run_task(_queue_item("t1", tmp_path))

    row = worker_db.get_one(tmp_path, "t1", _BRAND)
    assert row is not None
    assert row["status"] == "error"
    assert "no such file" in row["message"]


@requires_postgres
def test_run_task_surfaces_captured_output_on_timeout(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _timeout(cmd: list[str], **_kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd, 60, output="progress so far...", stderr="stderr line")

    monkeypatch.setattr(subprocess, "run", _timeout)

    with pytest.raises(subprocess.TimeoutExpired):
        task_worker.run_task(_queue_item("t1", tmp_path))

    row = worker_db.get_one(tmp_path, "t1", _BRAND)
    assert row is not None
    assert row["status"] == "error"
    assert "timed out after" in row["message"]
    assert "stderr line" in row["message"]


# --------------------------------------------------------------------------- _process_one


def test_process_one_notifies_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    notified: list[tuple[str, str]] = []

    def _boom(_task: dict[str, Any]) -> None:
        raise RuntimeError("execution failed")

    monkeypatch.setattr(task_worker, "run_task", _boom)
    monkeypatch.setattr(
        task_worker,
        "_notify_telegram_failure",
        lambda task_id, error: notified.append((task_id, error)),
    )

    task_worker._process_one({"schedule_task_id": "t1"})

    assert notified == [("t1", "execution failed")]


def test_process_one_does_not_notify_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    notified: list[tuple[str, str]] = []
    monkeypatch.setattr(task_worker, "run_task", lambda _task: None)
    monkeypatch.setattr(
        task_worker,
        "_notify_telegram_failure",
        lambda task_id, error: notified.append((task_id, error)),
    )

    task_worker._process_one({"schedule_task_id": "t1"})

    assert notified == []


# --------------------------------------------------------------------------- drain_once


def test_drain_once_processes_all_queued_items(monkeypatch: pytest.MonkeyPatch) -> None:
    processed: list[str] = []
    monkeypatch.setattr(
        task_worker,
        "_process_one",
        lambda task: processed.append(str(task["schedule_task_id"])),
    )

    queue = _FakeConsumerQueue(
        [{"schedule_task_id": "a"}, {"schedule_task_id": "b"}, {"schedule_task_id": "c"}]
    )
    count = task_worker.drain_once(queue)  # type: ignore[arg-type]

    assert count == 3
    assert processed == ["a", "b", "c"]


def test_drain_once_returns_zero_when_queue_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    processed: list[str] = []
    monkeypatch.setattr(task_worker, "_process_one", lambda task: processed.append("x"))

    count = task_worker.drain_once(_FakeConsumerQueue([]))  # type: ignore[arg-type]

    assert count == 0
    assert processed == []
