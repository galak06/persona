"""Tests for `api/reels_compose_api.py` -- the frontend reels-compose trigger.

The run itself executes in the WORKER container (ffmpeg + fonts live there,
not in the API image), so these tests fake the two collaborators that cross
that boundary: the Redis `flow-run` queue and the shared `worker_runs` row.
No Postgres, no Redis, no pipeline run.
"""
# ruff: noqa: S101, SLF001

from __future__ import annotations

from typing import Any

import pytest
from api import reels_compose_api
from fastapi import HTTPException

_BRAND = "b1"
_BRAND_DIR = "/app/brands/b1"
_LABEL = f"{_BRAND}-reels-compose"


@pytest.fixture(autouse=True)
def worker_state(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """In-memory stand-in for the shared `worker_runs` row + the queue."""
    state: dict[str, Any] = {"row": None, "pushed": []}

    monkeypatch.setattr("api.brand_context.brands_db.get", lambda _b: {"brand_dir": _BRAND_DIR})
    monkeypatch.setattr("lib.oauth.openart_store.resolve_brand_id", lambda: _BRAND)
    monkeypatch.setattr(reels_compose_api.worker_db, "get_one", lambda _d, _l, _b: state["row"])

    def _record_start(_dir: str, label: str, brand: str) -> None:
        state["row"] = {"status": "running", "last_run": "2026-08-12T00:00:00Z", "message": ""}
        state["started"] = (label, brand)

    monkeypatch.setattr(reels_compose_api.worker_db, "record_start", _record_start)

    class _FakeQueue:
        def __init__(self, worker: str, brand: str) -> None:
            state["queue"] = (worker, brand)

        def push(self, payload: dict[str, Any]) -> str:
            state["pushed"].append(payload)
            return "1"

    monkeypatch.setattr(reels_compose_api, "TaskQueue", _FakeQueue)
    return state


def _finish(state: dict[str, Any], *, status: str, message: str) -> None:
    """Simulate the worker completing the run."""
    state["row"] = {"status": status, "last_run": "2026-08-12T00:05:00Z", "message": message}


def test_compose_dispatches_to_the_worker_queue(worker_state: dict[str, Any]) -> None:
    """The API must ENQUEUE, not execute: the API image has no ffmpeg/fonts."""
    assert reels_compose_api.compose_reels() == {"status": "started"}

    assert worker_state["queue"] == ("flow-run", _BRAND)
    payload = worker_state["pushed"][0]
    assert payload["script"] == "scripts/crewai_reels_pipeline.py"
    assert payload["brand"] == _BRAND
    assert payload["brand_dir"] == _BRAND_DIR
    assert payload["schedule_task_id"] == _LABEL
    assert payload["timeout_seconds"] == 1800


def test_queued_run_reads_as_in_progress_not_a_failure(worker_state: dict[str, Any]) -> None:
    """A job waiting for the worker must not look like a failed run."""
    worker_state["row"] = {"status": "queued", "last_run": "2026-08-12T00:00:00Z", "message": ""}

    status = reels_compose_api.compose_status()

    assert status.running is True
    assert status.ok is None


def test_second_click_while_in_flight_gets_409(worker_state: dict[str, Any]) -> None:
    reels_compose_api.compose_reels()  # row now 'running'

    with pytest.raises(HTTPException) as exc:
        reels_compose_api.compose_reels()
    assert exc.value.status_code == 409
    assert len(worker_state["pushed"]) == 1  # not enqueued twice


def test_a_new_run_is_allowed_once_the_last_one_finished(
    worker_state: dict[str, Any],
) -> None:
    reels_compose_api.compose_reels()
    _finish(worker_state, status="success", message='summary: {"composed_openart": 1}')

    assert reels_compose_api.compose_reels() == {"status": "started"}
    assert len(worker_state["pushed"]) == 2


def test_status_with_no_history(worker_state: dict[str, Any]) -> None:
    status = reels_compose_api.compose_status()
    assert status.running is False
    assert status.ok is None


def test_worker_failure_is_reported(worker_state: dict[str, Any]) -> None:
    _finish(worker_state, status="error", message="exit=1: boom")

    status = reels_compose_api.compose_status()

    assert status.ok is False
    assert "boom" in (status.detail or "")


# ── OpenArt is optional, and reported honestly ────────────────────────────────
# History: an earlier revision pre-flighted OpenArt and refused the run with 503
# when the brand was unconfigured or unauthorized. That made an OPTIONAL image
# upgrade a hard prerequisite for a feature that composes fine without it, so
# the gate is gone: compose always runs, and OpenArt state is reported only as
# informational image counts.


def test_run_without_any_openart_setup_is_a_plain_success(
    worker_state: dict[str, Any],
) -> None:
    _finish(
        worker_state,
        status="success",
        message='summary: {"composed_fallback": 5, "ai_images": 0, "hero_images": 25}',
    )

    status = reels_compose_api.compose_status()

    assert status.ok is True  # a plain success, not a degraded one
    assert status.used_openart is False
    assert (status.ai_images, status.hero_images) == (0, 25)


def test_openart_run_is_reported_as_used(worker_state: dict[str, Any]) -> None:
    _finish(
        worker_state,
        status="success",
        message='summary: {"composed_openart": 3, "ai_images": 15, "hero_images": 0}',
    )

    status = reels_compose_api.compose_status()

    assert status.ok is True
    assert status.used_openart is True
    assert (status.ai_images, status.hero_images) == (15, 0)


def test_partly_ai_run_is_not_reported_as_pure_fallback(
    worker_state: dict[str, Any],
) -> None:
    """A reel with 4 AI beats and 1 hero beat must not read as "no OpenArt" --
    that mislabelling is what hid the discarded-images bug."""
    _finish(
        worker_state,
        status="success",
        message='summary: {"composed_mixed": 1, "ai_images": 4, "hero_images": 1}',
    )

    status = reels_compose_api.compose_status()

    assert status.ok is True
    assert status.used_openart is True
    assert (status.ai_images, status.hero_images) == (4, 1)


def test_status_has_no_auth_required_field() -> None:
    """The auth flag is gone on purpose: it read as a required user action for
    what is only an optional enhancement."""
    assert "auth_required" not in reels_compose_api.ComposeStatus.model_fields
