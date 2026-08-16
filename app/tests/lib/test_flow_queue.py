"""Tests for `lib.flow_queue` — the shared `flow-run` queue contract.

The payload shape crosses three processes (API or dispatcher → Redis →
`scripts/task_worker.py`) and was previously written by hand in five places.
Getting a field wrong fails quietly and at a distance: the item is enqueued,
the caller reports "started", and the run dies in another container.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib import flow_queue


def test_worker_name_is_single_sourced() -> None:
    """Five producers and the consumer used to declare this literal each."""
    assert flow_queue.FLOW_RUN_WORKER == "flow-run"


def test_payload_has_exactly_the_fields_the_worker_reads() -> None:
    payload = flow_queue.build_payload(
        schedule_task_id="dogfood-ig-engager",
        script="scripts/ig_engager.py",
        brand="dogfoodandfun",
        brand_dir="/app/brands/dogfoodandfun",
        args=["--dry-run"],
        timeout_seconds=600,
    )
    assert payload == {
        "schedule_task_id": "dogfood-ig-engager",
        "script": "scripts/ig_engager.py",
        "args": ["--dry-run"],
        "brand": "dogfoodandfun",
        "brand_dir": "/app/brands/dogfoodandfun",
        "timeout_seconds": 600,
    }


def test_headless_is_omitted_when_unset() -> None:
    """Absent means "use the brand's own runtime setting", not False."""
    payload = flow_queue.build_payload(
        schedule_task_id="x", script="s.py", brand="b", brand_dir="/d", timeout_seconds=1
    )
    assert "headless" not in payload


@pytest.mark.parametrize("value", [True, False])
def test_headless_is_carried_when_set(value: bool) -> None:
    payload = flow_queue.build_payload(
        schedule_task_id="x",
        script="s.py",
        brand="b",
        brand_dir="/d",
        timeout_seconds=1,
        headless=value,
    )
    assert payload["headless"] is value


def test_args_are_stringified() -> None:
    """`schedule_tasks.args` is JSONB and can hold non-strings."""
    payload = flow_queue.build_payload(
        schedule_task_id="x",
        script="s.py",
        brand="b",
        brand_dir="/d",
        args=[1, "--flag", 2.5],  # type: ignore[list-item]
        timeout_seconds=1,
    )
    assert payload["args"] == ["1", "--flag", "2.5"]


def test_brand_dir_is_stringified() -> None:
    """The worker reads it as a string; a Path would not survive JSON."""
    payload = flow_queue.build_payload(
        schedule_task_id="x",
        script="s.py",
        brand="b",
        brand_dir=Path("/app/brands/b"),
        timeout_seconds=1,
    )
    assert payload["brand_dir"] == "/app/brands/b"
    assert isinstance(payload["brand_dir"], str)


def test_payload_from_task_matches_build_payload() -> None:
    """The dispatcher's row-shaped entry point and the API's must agree.

    They were separate implementations; this is the property that used to be
    maintained by hand across five files.
    """
    row = {"id": "dogfood-ig-engager", "script": "scripts/ig_engager.py", "args": ["--dry-run"]}
    from_row = flow_queue.payload_from_task(
        row, brand="b", brand_dir=Path("/app/brands/b"), timeout_seconds=600
    )
    direct = flow_queue.build_payload(
        schedule_task_id="dogfood-ig-engager",
        script="scripts/ig_engager.py",
        args=["--dry-run"],
        brand="b",
        brand_dir="/app/brands/b",
        timeout_seconds=600,
    )
    assert from_row == direct


def test_payload_from_task_tolerates_missing_args() -> None:
    row = {"id": "x", "script": "s.py"}
    payload = flow_queue.payload_from_task(row, brand="b", brand_dir="/d", timeout_seconds=1)
    assert payload["args"] == []


def test_dispatch_pushes_to_the_flow_run_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Producers no longer name the worker, so they cannot name it wrongly."""
    seen: dict[str, Any] = {}

    class _FakeQueue:
        def __init__(self, worker: str, brand: str) -> None:
            seen["worker"] = worker
            seen["brand"] = brand

        def push(self, payload: dict[str, Any]) -> str:
            seen["payload"] = payload
            return "task-123"

    monkeypatch.setattr(flow_queue, "TaskQueue", _FakeQueue)

    task_id = flow_queue.dispatch(
        schedule_task_id="dogfood-fb-engager",
        script="scripts/fb_engager.py",
        brand="dogfoodandfun",
        brand_dir="/app/brands/dogfoodandfun",
        timeout_seconds=900,
    )

    assert task_id == "task-123"
    assert seen["worker"] == "flow-run"
    assert seen["brand"] == "dogfoodandfun"
    assert seen["payload"]["schedule_task_id"] == "dogfood-fb-engager"
    assert seen["payload"]["timeout_seconds"] == 900
