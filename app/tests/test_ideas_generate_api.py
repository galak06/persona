"""Tests for `api/ideas_generate_api.py` -- the frontend scout trigger.

The run itself executes in the WORKER container (Chromium lives there, not in
the API image), so these tests fake the two collaborators that cross that
boundary: the Redis `flow-run` queue and the shared `worker_runs` row. No
Postgres, no Redis, no scout run.
"""
# ruff: noqa: S101, SLF001

from __future__ import annotations

from typing import Any

import pytest
from api import ideas_generate_api
from fastapi import HTTPException

from lib import flow_queue

_BRAND = "b1"
_BRAND_DIR = "/app/brands/b1"
_LABEL = f"{_BRAND}-content-scout"


@pytest.fixture(autouse=True)
def worker_state(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """In-memory stand-in for the shared `worker_runs` row + the queue."""
    state: dict[str, Any] = {"row": None, "pushed": []}

    monkeypatch.setattr("api.brand_context.brands_db.get", lambda _b: {"brand_dir": _BRAND_DIR})
    monkeypatch.setattr("api.brand_context.current_brand_id", lambda: _BRAND)
    monkeypatch.setattr(ideas_generate_api.worker_db, "get_one", lambda _d, _l, _b: state["row"])

    def _record_start(_dir: str, label: str, brand: str) -> None:
        state["row"] = {"status": "running", "last_run": "2026-08-12T00:00:00Z", "message": ""}
        state["started"] = (label, brand)

    monkeypatch.setattr(ideas_generate_api.worker_db, "record_start", _record_start)

    class _FakeQueue:
        def __init__(self, worker: str, brand: str) -> None:
            state["queue"] = (worker, brand)

        def push(self, payload: dict[str, Any]) -> str:
            state["pushed"].append(payload)
            return "1"

    monkeypatch.setattr(flow_queue, "TaskQueue", _FakeQueue)
    return state


def _finish(state: dict[str, Any], *, status: str, message: str) -> None:
    """Simulate the worker completing the run."""
    state["row"] = {"status": status, "last_run": "2026-08-12T00:09:00Z", "message": message}


def test_generate_dispatches_to_the_worker_queue(worker_state: dict[str, Any]) -> None:
    """The API must ENQUEUE, not execute.

    `Dockerfile.api` has no `playwright install`, so the scout's Instagram
    scrape cannot launch Chromium here -- and that failed launch used to leave
    an event loop running that made crewai refuse both crews.
    """
    assert ideas_generate_api.generate_ideas() == {"status": "started"}

    assert worker_state["queue"] == ("flow-run", _BRAND)
    payload = worker_state["pushed"][0]
    assert payload["script"] == "scripts/crewai_content_scout.py"
    assert payload["brand"] == _BRAND
    assert payload["brand_dir"] == _BRAND_DIR
    assert payload["schedule_task_id"] == _LABEL
    assert payload["timeout_seconds"] == 900


def test_apply_flag_is_dispatched(worker_state: dict[str, Any]) -> None:
    """Without --apply the scout is a dry run and writes nothing."""
    ideas_generate_api.generate_ideas()

    assert worker_state["pushed"][0]["args"] == ["--apply"]


def test_queued_run_reads_as_in_progress_not_a_failure(worker_state: dict[str, Any]) -> None:
    """A job waiting for the worker must not look like a failed run."""
    worker_state["row"] = {"status": "queued", "last_run": "2026-08-12T00:00:00Z", "message": ""}

    status = ideas_generate_api.generate_status()

    assert status.running is True
    assert status.ok is None


def test_second_click_while_in_flight_gets_409(worker_state: dict[str, Any]) -> None:
    ideas_generate_api.generate_ideas()  # row now 'running'

    with pytest.raises(HTTPException) as exc:
        ideas_generate_api.generate_ideas()
    assert exc.value.status_code == 409
    assert len(worker_state["pushed"]) == 1  # the scout self-dedups; never race it


def test_a_new_run_is_allowed_once_the_last_one_finished(worker_state: dict[str, Any]) -> None:
    ideas_generate_api.generate_ideas()
    _finish(worker_state, status="success", message="8 candidate(s) scored, 6 inserted")

    assert ideas_generate_api.generate_ideas() == {"status": "started"}
    assert len(worker_state["pushed"]) == 2


def test_status_with_no_history(worker_state: dict[str, Any]) -> None:
    status = ideas_generate_api.generate_status()
    assert status.running is False
    assert status.ok is None


def test_successful_run_reports_the_scout_summary(worker_state: dict[str, Any]) -> None:
    _finish(worker_state, status="success", message="8 candidate(s) scored, 6 inserted")

    status = ideas_generate_api.generate_status()

    assert status.ok is True
    assert "6 inserted" in (status.detail or "")
    assert status.finished_at == "2026-08-12T00:09:00Z"


def test_worker_failure_is_reported(worker_state: dict[str, Any]) -> None:
    _finish(worker_state, status="error", message="exit=1: no ideas returned")

    status = ideas_generate_api.generate_status()

    assert status.ok is False
    assert "no ideas returned" in (status.detail or "")


def test_a_run_that_created_nothing_is_not_reported_as_success(
    worker_state: dict[str, Any],
) -> None:
    """The original bug, at the API boundary.

    The scout exited 0 when the crews died, the worker recorded `success`, and
    the Ideas page rendered "New ideas generated." over a run that inserted
    nothing. The scout now exits non-zero, which must surface as ok=False.
    """
    _finish(
        worker_state,
        status="error",
        message="exit=1: no ideas returned (see logs -- either the crew kickoff failed...)",
    )

    assert ideas_generate_api.generate_status().ok is False
