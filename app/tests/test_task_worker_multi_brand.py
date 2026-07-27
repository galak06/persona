"""Tests for `scripts/task_worker.py`'s shared, brand-agnostic worker model:
`run_task()`'s per-subprocess `env` build, and the multi-brand
`_active_brands()`/`drain_all_brands()` orchestration that replaced the
one-container-per-brand model. `run_task`'s core execution behavior
(success/error recording) is covered by `tests/test_task_worker.py` --
these tests only exercise what's new.
"""

from __future__ import annotations

import os
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

from lib import db
from lib.brands_db.models import BrandStatus

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


def _queue_item(schedule_task_id: str, brand_dir: Path) -> dict[str, Any]:
    return {
        "schedule_task_id": schedule_task_id,
        "script": "scripts/noop_healthcheck.py",
        "args": [],
        "brand": _BRAND,
        "brand_dir": str(brand_dir),
        "timeout_seconds": 60,
    }


def _capturing_run(captured_env: dict[str, str]) -> Any:
    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured_env.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    return _run


# --------------------------------------------------------------------------- run_task env=


@requires_postgres
def test_run_task_builds_env_from_task_payload_not_process_env(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setattr(subprocess, "run", _capturing_run(captured))
    monkeypatch.setenv("PERSONA_BRAND", "wrong-brand")
    monkeypatch.setenv("BRAND_DIR", "/wrong/dir")

    task_worker.run_task(_queue_item("t1", tmp_path))

    assert captured["BRAND_DIR"] == str(tmp_path)
    assert captured["PERSONA_BRAND"] == _BRAND


@requires_postgres
def test_run_task_merges_brand_dotenv_credentials_into_subprocess_env(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("FB_PAGE_TOKEN=secret123\n")
    captured: dict[str, str] = {}
    monkeypatch.setattr(subprocess, "run", _capturing_run(captured))

    task_worker.run_task(_queue_item("t1", tmp_path))

    assert captured["FB_PAGE_TOKEN"] == "secret123"


@requires_postgres
def test_run_task_does_not_leak_brand_env_into_os_environ(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("SOME_BRAND_SECRET=leak-test\n")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr=""),
    )

    task_worker.run_task(_queue_item("t1", tmp_path))

    assert "SOME_BRAND_SECRET" not in os.environ


@requires_postgres
def test_run_task_headless_override_sets_playwright_env(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`headless: false` in the queue payload (from the Run Now checkbox)
    overrides the worker container's own PLAYWRIGHT_HEADLESS for this task."""
    captured: dict[str, str] = {}
    monkeypatch.setattr(subprocess, "run", _capturing_run(captured))
    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "1")

    task = _queue_item("t1", tmp_path)
    task["headless"] = False
    task_worker.run_task(task)

    assert captured["PLAYWRIGHT_HEADLESS"] == "0"


@requires_postgres
def test_run_task_without_headless_field_leaves_env_untouched(
    pg: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setattr(subprocess, "run", _capturing_run(captured))
    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "1")

    task_worker.run_task(_queue_item("t1", tmp_path))

    assert captured["PLAYWRIGHT_HEADLESS"] == "1"


# --------------------------------------------------------------------------- multi-brand orchestration


def test_active_brands_filters_to_provisioned_and_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        task_worker.brands_db,
        "list_brands",
        lambda: [
            {"id": "draft-brand", "status": BrandStatus.DRAFT},
            {"id": "disabled-brand", "status": BrandStatus.DISABLED},
            {"id": "provisioned-brand", "status": BrandStatus.PROVISIONED},
            {"id": "active-brand", "status": BrandStatus.ACTIVE},
        ],
    )

    result = [b["id"] for b in task_worker._active_brands()]

    assert result == ["provisioned-brand", "active-brand"]


def test_drain_all_brands_processes_every_brands_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        task_worker.brands_db,
        "list_brands",
        lambda: [
            {"id": "brand-a", "status": BrandStatus.PROVISIONED},
            {"id": "brand-b", "status": BrandStatus.ACTIVE},
        ],
    )
    items_by_brand = {
        "brand-a": [{"schedule_task_id": "a1"}],
        "brand-b": [{"schedule_task_id": "b1"}, {"schedule_task_id": "b2"}],
    }

    class _FakeQueue:
        def __init__(self, brand: str) -> None:
            self._items = items_by_brand[brand]

        def pop_nowait(self) -> dict[str, Any] | None:
            return self._items.pop(0) if self._items else None

    monkeypatch.setattr(task_worker, "TaskQueue", lambda *, worker, brand: _FakeQueue(brand))
    processed: list[str] = []
    monkeypatch.setattr(
        task_worker, "_process_one", lambda task: processed.append(task["schedule_task_id"])
    )

    total = task_worker.drain_all_brands()

    assert total == 3
    assert sorted(processed) == ["a1", "b1", "b2"]
