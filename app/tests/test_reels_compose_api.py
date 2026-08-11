"""Tests for `api/reels_compose_api.py` -- the frontend reels-compose trigger.

Same convention as `test_ideas_generate_api.py`: `subprocess.run` faked via
monkeypatch, plain function calls, no TestClient and no live pipeline run.
The module-level lock is real -- that's the behavior under test.
"""
# ruff: noqa: S101

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from api import reels_compose_api
from fastapi import BackgroundTasks, HTTPException


@pytest.fixture(autouse=True)
def _reset_state() -> Any:
    """Each test starts with no run in flight and no history."""
    if reels_compose_api._run_lock.locked():  # noqa: SLF001
        reels_compose_api._run_lock.release()  # noqa: SLF001
    reels_compose_api._last_run = {}  # noqa: SLF001
    yield
    if reels_compose_api._run_lock.locked():  # noqa: SLF001
        reels_compose_api._run_lock.release()  # noqa: SLF001


def _fake_run(returncode: int = 0, stdout: str = "summary: composed 2 reels") -> Any:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="boom")

    _run.calls = calls  # type: ignore[attr-defined]
    return _run


def _drain(tasks: BackgroundTasks) -> None:
    for task in tasks.tasks:
        task.func(*task.args, **task.kwargs)


def test_compose_runs_the_reels_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_run()
    monkeypatch.setattr(subprocess, "run", fake)
    tasks = BackgroundTasks()

    assert reels_compose_api.compose_reels(tasks) == {"status": "started"}
    _drain(tasks)

    assert len(fake.calls) == 1
    assert "scripts.crewai_reels_pipeline" in fake.calls[0]

    status = reels_compose_api.compose_status()
    assert status.running is False
    assert status.ok is True
    assert "composed 2 reels" in (status.detail or "")


def test_second_click_while_running_gets_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lock is taken by the route (immediate 409), released by the
    background task -- so between the two, a second POST must bounce."""
    monkeypatch.setattr(subprocess, "run", _fake_run())
    tasks = BackgroundTasks()
    reels_compose_api.compose_reels(tasks)  # lock now held, task not yet drained

    with pytest.raises(HTTPException) as exc:
        reels_compose_api.compose_reels(BackgroundTasks())
    assert exc.value.status_code == 409

    _drain(tasks)  # run completes, lock released
    tasks2 = BackgroundTasks()
    assert reels_compose_api.compose_reels(tasks2) == {"status": "started"}
    _drain(tasks2)


def test_failure_is_reported_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(returncode=1))
    tasks = BackgroundTasks()
    reels_compose_api.compose_reels(tasks)
    _drain(tasks)

    status = reels_compose_api.compose_status()
    assert status.ok is False
    assert "boom" in (status.detail or "")


def test_timeout_releases_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung pipeline must not wedge the button forever."""

    def _timeout(cmd: list[str], **_kw: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd, 1800)

    monkeypatch.setattr(subprocess, "run", _timeout)
    tasks = BackgroundTasks()
    reels_compose_api.compose_reels(tasks)
    _drain(tasks)

    status = reels_compose_api.compose_status()
    assert status.ok is False
    assert "timed out" in (status.detail or "")
    # And a new run is possible:
    monkeypatch.setattr(subprocess, "run", _fake_run())
    tasks2 = BackgroundTasks()
    assert reels_compose_api.compose_reels(tasks2) == {"status": "started"}
    _drain(tasks2)


def test_status_with_no_history() -> None:
    status = reels_compose_api.compose_status()
    assert status.running is False
    assert status.ok is None
