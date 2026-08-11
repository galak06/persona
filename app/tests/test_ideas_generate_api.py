"""Tests for `api/ideas_generate_api.py` -- the frontend scout trigger.

Same convention as the other route tests: `subprocess.run` faked via
monkeypatch, plain function calls, no TestClient and no live scout run.
The module-level lock is real -- that's the behavior under test.
"""
# ruff: noqa: S101

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from api import ideas_generate_api
from fastapi import BackgroundTasks, HTTPException


@pytest.fixture(autouse=True)
def _reset_state() -> Any:
    """Each test starts with no run in flight and no history."""
    if ideas_generate_api._run_lock.locked():  # noqa: SLF001
        ideas_generate_api._run_lock.release()  # noqa: SLF001
    ideas_generate_api._last_run = {}  # noqa: SLF001
    yield
    if ideas_generate_api._run_lock.locked():  # noqa: SLF001
        ideas_generate_api._run_lock.release()  # noqa: SLF001


def _fake_run(returncode: int = 0, stdout: str = "summary: wrote 8 ideas") -> Any:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="boom")

    _run.calls = calls  # type: ignore[attr-defined]
    return _run


def _drain(tasks: BackgroundTasks) -> None:
    for task in tasks.tasks:
        task.func(*task.args, **task.kwargs)


def test_generate_runs_scout_with_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_run()
    monkeypatch.setattr(subprocess, "run", fake)
    tasks = BackgroundTasks()

    assert ideas_generate_api.generate_ideas(tasks) == {"status": "started"}
    _drain(tasks)

    assert len(fake.calls) == 1
    cmd = fake.calls[0]
    assert "scripts.crewai_content_scout" in cmd
    assert "--apply" in cmd  # without it the scout is a dry run and writes nothing

    status = ideas_generate_api.generate_status()
    assert status.running is False
    assert status.ok is True
    assert "wrote 8 ideas" in (status.detail or "")


def test_second_click_while_running_gets_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lock is taken by the route (immediate 409), released by the
    background task -- so between the two, a second POST must bounce."""
    monkeypatch.setattr(subprocess, "run", _fake_run())
    tasks = BackgroundTasks()
    ideas_generate_api.generate_ideas(tasks)  # lock now held, task not yet drained

    with pytest.raises(HTTPException) as exc:
        ideas_generate_api.generate_ideas(BackgroundTasks())
    assert exc.value.status_code == 409

    _drain(tasks)  # run completes, lock released
    tasks2 = BackgroundTasks()
    assert ideas_generate_api.generate_ideas(tasks2) == {"status": "started"}
    _drain(tasks2)


def test_failure_is_reported_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(returncode=1))
    tasks = BackgroundTasks()
    ideas_generate_api.generate_ideas(tasks)
    _drain(tasks)

    status = ideas_generate_api.generate_status()
    assert status.ok is False
    assert "boom" in (status.detail or "")


def test_timeout_releases_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung scout must not wedge the button forever."""

    def _timeout(cmd: list[str], **_kw: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd, 900)

    monkeypatch.setattr(subprocess, "run", _timeout)
    tasks = BackgroundTasks()
    ideas_generate_api.generate_ideas(tasks)
    _drain(tasks)

    status = ideas_generate_api.generate_status()
    assert status.ok is False
    assert "timed out" in (status.detail or "")
    # And a new run is possible:
    monkeypatch.setattr(subprocess, "run", _fake_run())
    tasks2 = BackgroundTasks()
    assert ideas_generate_api.generate_ideas(tasks2) == {"status": "started"}
    _drain(tasks2)


def test_status_with_no_history() -> None:
    status = ideas_generate_api.generate_status()
    assert status.running is False
    assert status.ok is None
