"""Tests for `api/social_posts_compose_api.py` -- the Compose posts button.

Fakes the two collaborators that cross the API/worker boundary (the Redis
`flow-run` queue and the shared `worker_runs` row) plus the `schedule_tasks`
lookup. No Postgres, no Redis, no pipeline run.
"""
# ruff: noqa: S101, SLF001

from __future__ import annotations

from typing import Any

import pytest
from api import social_posts_compose_api as compose_api
from fastapi import HTTPException

_BRAND = "b1"
_BRAND_DIR = "/app/brands/b1"
_ROW_ID = "legacy-social-posts-compose"  # ids don't have to match the brand id


@pytest.fixture(autouse=True)
def worker_state(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """In-memory stand-in for the `worker_runs` row, the queue and the
    provisioned `schedule_tasks` row."""
    state: dict[str, Any] = {
        "row": None,
        "pushed": [],
        "tasks": [
            {
                "id": _ROW_ID,
                "title": "social-posts-compose",
                "brand_id": _BRAND,
                "args": ["--compose-only"],
                "timeout_minutes": 30,
            }
        ],
    }

    state["candidates"] = [{"id": "idea-1"}]

    monkeypatch.setattr("api.brand_context.brands_db.get", lambda _b: {"brand_dir": _BRAND_DIR})
    monkeypatch.setattr("lib.oauth.openart_store.resolve_brand_id", lambda: _BRAND)
    monkeypatch.setattr(compose_api.schedule_db, "load_all", lambda: state["tasks"])
    monkeypatch.setattr(compose_api.worker_db, "get_one", lambda _d, _l, _b: state["row"])
    monkeypatch.setattr(
        compose_api.social_post_db,
        "list_candidates",
        lambda **_kw: state["candidates"],
    )

    def _record_start(_dir: str, label: str, brand: str) -> None:
        state["row"] = {"status": "running", "last_run": "2026-08-14T00:00:00Z", "message": ""}
        state["started"] = (label, brand)

    monkeypatch.setattr(compose_api.worker_db, "record_start", _record_start)

    class _FakeQueue:
        def __init__(self, worker: str, brand: str) -> None:
            state["queue"] = (worker, brand)

        def push(self, payload: dict[str, Any]) -> str:
            state["pushed"].append(payload)
            return "1"

    monkeypatch.setattr(compose_api, "TaskQueue", _FakeQueue)
    return state


def _finish(state: dict[str, Any], *, status: str, message: str) -> None:
    """Simulate the worker completing the run."""
    state["row"] = {"status": status, "last_run": "2026-08-14T00:05:00Z", "message": message}


def test_compose_dispatches_the_scheduled_row_to_the_worker(worker_state: dict[str, Any]) -> None:
    """Button and cron must be the SAME run: same label, args and timeout as
    the provisioned row, enqueued rather than executed in the API image."""
    assert compose_api.compose_social_posts() == {"status": "started"}

    assert worker_state["queue"] == ("flow-run", _BRAND)
    payload = worker_state["pushed"][0]
    assert payload["script"] == "scripts/crewai_social_posts_pipeline.py"
    assert payload["schedule_task_id"] == _ROW_ID
    assert payload["args"] == ["--compose-only"]
    assert payload["timeout_seconds"] == 1800
    assert payload["brand"] == _BRAND
    assert payload["brand_dir"] == _BRAND_DIR


@pytest.mark.parametrize(
    "row_args",
    [
        pytest.param([], id="no args = the argless compose-AND-release mode"),
        pytest.param(["--release-only"], id="a row pointed at the publisher"),
        pytest.param(["--limit", "2"], id="a tuned row with no mode flag"),
    ],
)
def test_the_button_can_never_publish(worker_state: dict[str, Any], row_args: list[str]) -> None:
    """`--compose-only` is the whole point: publishing stays owned by
    `social-posts-release`, whatever the row says."""
    worker_state["tasks"][0]["args"] = row_args

    compose_api.compose_social_posts()

    dispatched = worker_state["pushed"][0]["args"]
    assert "--compose-only" in dispatched
    assert "--release-only" not in dispatched


def test_unprovisioned_brand_still_composes(worker_state: dict[str, Any]) -> None:
    """No `schedule_tasks` row (brand never backfilled) falls back to this
    module's own constants instead of 404-ing the button."""
    worker_state["tasks"] = []

    compose_api.compose_social_posts()

    payload = worker_state["pushed"][0]
    assert payload["schedule_task_id"] == f"{_BRAND}-social-posts-compose"
    assert payload["args"] == ["--compose-only"]


def test_second_click_while_in_flight_gets_409(worker_state: dict[str, Any]) -> None:
    """Covers the cron run too -- it writes the same `worker_runs` row."""
    compose_api.compose_social_posts()  # row now 'running'

    with pytest.raises(HTTPException) as exc:
        compose_api.compose_social_posts()
    assert exc.value.status_code == 409
    assert len(worker_state["pushed"]) == 1  # not enqueued twice


def test_queued_run_reads_as_in_progress_not_a_failure(worker_state: dict[str, Any]) -> None:
    worker_state["row"] = {"status": "queued", "last_run": "2026-08-14T00:00:00Z", "message": ""}

    status = compose_api.compose_status()

    assert status.running is True
    assert status.ok is None


def test_status_with_no_history(worker_state: dict[str, Any]) -> None:
    status = compose_api.compose_status()
    assert status.running is False
    assert status.ok is None


def test_composed_count_is_read_from_the_summary(worker_state: dict[str, Any]) -> None:
    """The pipeline keys its count by image source, so both variants count."""
    _finish(
        worker_state,
        status="success",
        message='summary: {"composed_gemini": 2, "composed_fallback": 1}',
    )

    status = compose_api.compose_status()

    assert status.ok is True
    assert status.composed == 3


def test_a_run_that_found_no_candidates_is_still_a_success(
    worker_state: dict[str, Any],
) -> None:
    """Every published article already having posts is the normal steady
    state -- it must not read as a failed run."""
    _finish(worker_state, status="success", message="summary: {}")

    status = compose_api.compose_status()

    assert status.ok is True
    assert status.composed == 0


def test_status_reports_what_a_run_would_have_to_work_with(
    worker_state: dict[str, Any],
) -> None:
    """A successful run that composes nothing is indistinguishable from a dead
    button, so the count that explains it is reported BEFORE the click."""
    worker_state["candidates"] = [{"id": "a"}, {"id": "b"}]

    assert compose_api.compose_status().candidates == 2


def test_no_candidates_reads_as_zero_not_missing(worker_state: dict[str, Any]) -> None:
    """Zero drives the button's disabled state, so it must be a real 0 rather
    than a null the UI would render as "unknown"."""
    worker_state["candidates"] = []
    _finish(worker_state, status="success", message="summary: {}")

    status = compose_api.compose_status()

    assert status.candidates == 0
    assert status.ok is True  # nothing to do is still a successful run
    assert status.composed == 0


def test_worker_failure_is_reported(worker_state: dict[str, Any]) -> None:
    _finish(worker_state, status="error", message="exit=1: boom")

    status = compose_api.compose_status()

    assert status.ok is False
    assert "boom" in (status.detail or "")


def test_a_new_run_is_allowed_once_the_last_one_finished(
    worker_state: dict[str, Any],
) -> None:
    compose_api.compose_social_posts()
    _finish(worker_state, status="success", message='summary: {"composed_gemini": 1}')

    assert compose_api.compose_social_posts() == {"status": "started"}
    assert len(worker_state["pushed"]) == 2
