"""Tests for the `trigger_worker()` helpers extracted from `api/approval_api.py`
during its complexity-reduction refactor: `_build_trigger_command`,
`_enforce_daily_rerun_guard`, `_wrap_for_macos_session`, `_find_occupied_pids`.

Pure/near-pure unit tests -- no live Postgres, no real subprocess spawning
(that stays covered by manual smoke-testing the live endpoint, since fully
exercising `trigger_worker()` end-to-end needs a real process tree).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import HTTPException

from api import approval_api


@dataclass
class _FakeTask:
    id: str
    skill: str | None = None


def _body(**overrides: object) -> approval_api._TriggerBody:
    return approval_api._TriggerBody(**overrides)


# --------------------------------------------------------------------- _build_trigger_command


def test_build_trigger_command_normalizes_python_prefix() -> None:
    task = _FakeTask(id="t1")
    cmd = approval_api._build_trigger_command(task, {"script": "python scripts/ig_engager.py"}, _body())
    assert cmd == [sys.executable, "scripts/ig_engager.py"]


def test_build_trigger_command_appends_force_and_recipe_ids() -> None:
    task = _FakeTask(id="t1")
    cmd = approval_api._build_trigger_command(
        task, {"script": "scripts/publish.py"}, _body(force=True, recipe_ids=["r1", "r2"])
    )
    assert "--force" in cmd
    assert cmd.count("--seed") == 2
    assert "r1" in cmd and "r2" in cmd


def test_build_trigger_command_falls_back_to_skill() -> None:
    task = _FakeTask(id="t1", skill="ig-engager")
    cmd = approval_api._build_trigger_command(task, {}, _body())
    assert cmd[-1] == "/ig-engager"
    assert "--dangerously-skip-permissions" in cmd


def test_build_trigger_command_returns_none_when_neither_defined() -> None:
    task = _FakeTask(id="t1")
    assert approval_api._build_trigger_command(task, {}, _body()) is None


# --------------------------------------------------------------- _enforce_daily_rerun_guard


def test_enforce_rerun_guard_raises_when_already_ran_successfully_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today = approval_api.datetime.date.today().isoformat()
    monkeypatch.setattr(
        approval_api,
        "worker_db_get_one",
        lambda *_a, **_k: {"status": "success", "last_run": f"{today}T10:00:00"},
    )
    with pytest.raises(HTTPException) as exc_info:
        approval_api._enforce_daily_rerun_guard({}, Path("/tmp/brand"), "t1", force=False)
    assert exc_info.value.status_code == 409


def test_enforce_rerun_guard_allows_when_bypassed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both bypass conditions (force=True, re_run_guard=0) short-circuit
    before ever consulting worker_db."""
    monkeypatch.setattr(
        approval_api, "worker_db_get_one", lambda *_a, **_k: pytest.fail("must not be called")
    )
    approval_api._enforce_daily_rerun_guard({}, Path("/tmp/brand"), "t1", force=True)
    approval_api._enforce_daily_rerun_guard({"re_run_guard": 0}, Path("/tmp/brand"), "t1", force=False)


def test_enforce_rerun_guard_allows_when_no_prior_success_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approval_api, "worker_db_get_one", lambda *_a, **_k: None)
    approval_api._enforce_daily_rerun_guard({}, Path("/tmp/brand"), "t1", force=False)


# ------------------------------------------------------------------- _wrap_for_macos_session


def test_wrap_for_macos_session_passes_through_on_non_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approval_api.platform, "system", lambda: "Linux")
    cmd = ["scripts/ig_engager.py"]
    assert approval_api._wrap_for_macos_session(cmd, None) == cmd


def test_wrap_for_macos_session_wraps_with_launchctl_on_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approval_api.platform, "system", lambda: "Darwin")
    cmd = approval_api._wrap_for_macos_session(["scripts/ig_engager.py"], headless=True)
    assert cmd[:2] == ["launchctl", "asuser"]
    assert "PLAYWRIGHT_HEADLESS=1" in cmd


# ------------------------------------------------------------------------ _find_occupied_pids


def test_find_occupied_pids_empty_when_no_pid_files(tmp_path: Path) -> None:
    (tmp_path / "logs").mkdir()
    assert approval_api._find_occupied_pids(tmp_path, "ig_engager", count=1) == []


def test_find_occupied_pids_cleans_up_stale_pid_file(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    pid_file = logs / "ig_engager.pid"
    pid_file.write_text("999999999")  # not a real running pid

    result = approval_api._find_occupied_pids(tmp_path, "ig_engager", count=1)

    assert result == []
    assert not pid_file.exists()


def test_find_occupied_pids_detects_a_live_pid(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "ig_engager.pid").write_text(str(os.getpid()))

    assert approval_api._find_occupied_pids(tmp_path, "ig_engager", count=1) == [os.getpid()]
