"""Tests for `scripts/task_dispatcher.py`'s `enabled_flows` gate (PR6).

Split out of `test_task_dispatcher.py` to keep that file under the
project's 300-line cap. Reuses its `_task_row`/`_FakeLock`/`_FakeQueue`
helpers and `requires_postgres` skipif convention. Since PR7,
`run_once`/`dispatch_task` enqueue rather than execute -- gate assertions
below check `_FakeQueue.pushed`, not a subprocess call.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import task_dispatcher

from lib import db, schedule_db, worker_db
from lib.brands_db.repository import BrandsRepository
from tests.test_task_dispatcher import _FakeLock, _FakeQueue, _task_row, pg, requires_postgres

__all__ = ["pg"]  # re-exported fixture, used implicitly as a test parameter

_FLOW_GATE_BRAND = "flow-gate-test-brand"


@pytest.fixture
def flow_gate_brand() -> Iterator[None]:
    """A `brands` row scoped to exactly `_FLOW_GATE_BRAND`, with only
    `ig-scanner` enabled. Cleaned up via a targeted `DELETE ... WHERE id =`
    (never a blanket `TRUNCATE`) so this fixture only ever touches the one
    row it created -- safe to run alongside any other data in `brands`.
    """
    BrandsRepository().create(
        brand_id=_FLOW_GATE_BRAND,
        name="Flow Gate Test",
        site_url="https://flow-gate-test.example",
        niche="n",
        enabled_flows=["ig-scanner"],
    )
    try:
        yield
    finally:
        db.execute("DELETE FROM brands WHERE id = %s", (_FLOW_GATE_BRAND,))


# --------------------------------------------------------------- _flow_enabled (pure, no infra)


def test_flow_enabled_true_for_unmanaged_flow() -> None:
    """A row whose flow id isn't one of the 3 onboarding-managed flows
    (e.g. a legacy WP/recipe schedule) is never gated by enabled_flows."""
    assert task_dispatcher._flow_enabled({"title": "some-legacy-flow"}, frozenset()) is True


def test_flow_enabled_true_when_enabled_flows_unknown() -> None:
    """Fails open when the brand row couldn't be read (enabled_flows=None)."""
    assert task_dispatcher._flow_enabled({"title": "fb-group-scout"}, None) is True


def test_flow_enabled_false_for_disabled_managed_flow() -> None:
    assert (
        task_dispatcher._flow_enabled({"title": "fb-group-scout"}, frozenset({"ig-scanner"}))
        is False
    )


def test_flow_enabled_true_for_enabled_managed_flow() -> None:
    assert task_dispatcher._flow_enabled({"title": "ig-scanner"}, frozenset({"ig-scanner"})) is True


# --------------------------------------------------------------- run_once + enabled_flows


@requires_postgres
def test_run_once_skips_disabled_managed_flow(
    pg: None, flow_gate_brand: None, tmp_path: Path
) -> None:
    """`fb-group-scout` is absent from `_FLOW_GATE_BRAND`'s `enabled_flows`
    -- its row is skipped even though it's due, while the sibling
    `ig-scanner` row (which IS enabled) still gets enqueued normally."""
    queue = _FakeQueue()

    ig_task = _task_row("ig", _FLOW_GATE_BRAND)
    ig_task["title"] = "ig-scanner"
    scout_task = _task_row("scout", _FLOW_GATE_BRAND, script="scripts/fb_group_scout.py")
    scout_task["title"] = "fb-group-scout"
    schedule_db.save_task(None, ig_task)
    schedule_db.save_task(None, scout_task)

    task_dispatcher.run_once(
        brand=_FLOW_GATE_BRAND,
        brand_dir=tmp_path,
        now=datetime.now(UTC),
        redis_client=_FakeLock(),
        queue=queue,
    )

    assert [p["schedule_task_id"] for p in queue.pushed] == ["ig"]
    assert worker_db.get_one(tmp_path, "ig", _FLOW_GATE_BRAND) is None
    assert worker_db.get_one(tmp_path, "scout", _FLOW_GATE_BRAND) is None
