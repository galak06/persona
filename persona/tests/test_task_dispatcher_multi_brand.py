"""Tests for `scripts/task_dispatcher.py::run_all_brands()` -- the shared,
brand-agnostic dispatcher entry point that replaces the one-container-per-
brand model. `run_once()` itself is untouched (see `tests/test_task_dispatcher.py`);
these tests only exercise the new per-brand iteration + `brands.brand_dir`
resolution layered on top of it.

Real integration tests against a live local Postgres, following the
project's `requires_postgres` skipif convention.
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

from lib import brands_db, db, schedule_db
from lib.brands_db.models import BrandStatus

_SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"


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
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE schedule_tasks, worker_runs, brands CASCADE")


class _FakeLock:
    def __init__(self) -> None:
        self._held: set[str] = set()

    def set(self, name: str, value: str, *, nx: bool = False, ex: int | None = None) -> Any:
        if nx and name in self._held:
            return None
        self._held.add(name)
        return True


def _task_row(task_id: str, brand_id: str) -> dict[str, Any]:
    return {
        "id": task_id,
        "brand_id": brand_id,
        "script": "scripts/noop_healthcheck.py",
        "schedule": {"cron": "* * * * *"},
        "args": [],
        "order_num": 0,
    }


def _make_brand(brand_id: str, brand_dir: str, *, status: str = BrandStatus.PROVISIONED) -> None:
    brands_db.create(
        brand_id=brand_id,
        name=brand_id,
        site_url="https://example.com",
        niche="test",
        status=status,
        brand_dir=brand_dir,
    )


@requires_postgres
def test_run_all_brands_skips_brand_with_no_brand_dir(
    pg: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_brand("no-dir-brand", "")
    schedule_db.save_task(None, _task_row("orphan-task", "no-dir-brand"))

    calls: list[str] = []
    monkeypatch.setattr(
        task_dispatcher,
        "run_once",
        lambda **kwargs: calls.append(kwargs["brand"]),
    )

    task_dispatcher.run_all_brands(now=datetime.now(UTC), redis_client=_FakeLock())

    assert calls == []


@requires_postgres
def test_run_all_brands_skips_draft_and_disabled_brands(
    pg: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_brand("draft-brand", "/brands/draft-brand", status=BrandStatus.DRAFT)
    _make_brand("disabled-brand", "/brands/disabled-brand", status=BrandStatus.DISABLED)
    _make_brand("active-brand", "/brands/active-brand", status=BrandStatus.PROVISIONED)

    calls: list[str] = []
    monkeypatch.setattr(
        task_dispatcher,
        "run_once",
        lambda **kwargs: calls.append(kwargs["brand"]),
    )

    task_dispatcher.run_all_brands(now=datetime.now(UTC), redis_client=_FakeLock())

    assert calls == ["active-brand"]


@requires_postgres
def test_run_all_brands_calls_run_once_with_brand_dir_from_postgres(
    pg: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_brand("brand-a", "/brands/brand-a")
    _make_brand("brand-b", "/brands/brand-b")

    seen: dict[str, Path] = {}
    monkeypatch.setattr(
        task_dispatcher,
        "run_once",
        lambda **kwargs: seen.__setitem__(kwargs["brand"], kwargs["brand_dir"]),
    )

    task_dispatcher.run_all_brands(now=datetime.now(UTC), redis_client=_FakeLock())

    assert seen == {
        "brand-a": Path("/brands/brand-a"),
        "brand-b": Path("/brands/brand-b"),
    }
