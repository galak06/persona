"""Tests for `api/ideas_api.py::_draft_idea_with_crewai_background` -- the
BackgroundTasks callback that PATCH /ideas/{id}/status fires when an idea
transitions to "approved". The function first atomically claims the idea via
`ideas_db.claim_idea_for_drafting` (the concurrency guard against a parallel
`worker_wp_ideas.py` sweep claiming the same idea); only on a won claim does
it run `scripts.crewai_content_pipeline` via `subprocess.run`. Here the
subprocess call is faked out via `monkeypatch.setattr(subprocess, "run", ...)`
(the project's established convention for subprocess-spawning code -- see
tests/test_task_worker.py), and `lib.ideas_db.claim_idea_for_drafting` /
`get_idea` / `update_status` are all faked at the module level so no live
Postgres is required.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

import pytest
from api import ideas_api

_IDEA_ID = "idea-1"


def _fake_claim(result: bool = True) -> Any:
    calls: list[str] = []

    def _claim(idea_id: str) -> bool:
        calls.append(idea_id)
        return result

    _claim.calls = calls  # type: ignore[attr-defined]
    return _claim


def _fake_get_idea(status: str | None) -> Any:
    """``status=None`` simulates a get_idea() miss (idea not found / DB error)."""
    calls: list[str] = []

    def _get_idea(idea_id: str) -> dict[str, Any] | None:
        calls.append(idea_id)
        if status is None:
            return None
        return {"id": idea_id, "status": status}

    _get_idea.calls = calls  # type: ignore[attr-defined]
    return _get_idea


def _fake_update_status() -> Any:
    calls: list[tuple[str, str]] = []

    def _update_status(idea_id: str, status: str) -> bool:
        calls.append((idea_id, status))
        return True

    _update_status.calls = calls  # type: ignore[attr-defined]
    return _update_status


def _fake_run(returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    _run.calls = calls  # type: ignore[attr-defined]
    return _run


def _log_payloads(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, Any]]:
    return [
        json.loads(r.msg)
        for r in caplog.records
        if r.name == ideas_api.log.name and event in str(r.msg)
    ]


# --------------------------------------------------------------- claim lost (not approved)


def test_draft_idea_skipped_when_claim_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If claim_idea_for_drafting can't win (idea no longer status='approved' --
    e.g. a Disable race, or a concurrent worker_wp_ideas.py sweep already
    claimed it), the subprocess must never be spawned."""
    fake_claim = _fake_claim(False)
    fake_run = _fake_run(returncode=0)
    monkeypatch.setattr(ideas_api.ideas_db, "claim_idea_for_drafting", fake_claim)
    monkeypatch.setattr(subprocess, "run", fake_run)

    with caplog.at_level(logging.INFO, logger=ideas_api.log.name):
        ideas_api._draft_idea_with_crewai_background(_IDEA_ID)

    assert fake_claim.calls == [_IDEA_ID]
    assert fake_run.calls == []
    assert any("idea_draft_skipped_not_approved" in str(r.msg) for r in caplog.records)


# --------------------------------------------------------------- claim won + success


def test_draft_idea_success_logs_and_invokes_subprocess_no_revert(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake_claim = _fake_claim(True)
    fake_run = _fake_run(returncode=0)
    fake_get_idea = _fake_get_idea("wp_draft")
    fake_update_status = _fake_update_status()
    monkeypatch.setattr(ideas_api.ideas_db, "claim_idea_for_drafting", fake_claim)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(ideas_api.ideas_db, "get_idea", fake_get_idea)
    monkeypatch.setattr(ideas_api.ideas_db, "update_status", fake_update_status)

    with caplog.at_level(logging.INFO, logger=ideas_api.log.name):
        ideas_api._draft_idea_with_crewai_background(_IDEA_ID)

    assert len(fake_run.calls) == 1
    cmd = fake_run.calls[0]
    assert "scripts.crewai_content_pipeline" in cmd
    assert cmd[-2:] == ["--idea-id", _IDEA_ID]
    assert any("idea_drafted" in str(r.msg) for r in caplog.records)
    # success path never calls the revert helper at all
    assert fake_get_idea.calls == []
    assert fake_update_status.calls == []


# --------------------------------------------------------------- non-zero exit, still "drafting"


def test_draft_idea_failure_reverts_stuck_drafting_claim(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake_claim = _fake_claim(True)
    fake_run = _fake_run(returncode=1, stdout="partial output", stderr="boom")
    fake_get_idea = _fake_get_idea("drafting")
    fake_update_status = _fake_update_status()
    monkeypatch.setattr(ideas_api.ideas_db, "claim_idea_for_drafting", fake_claim)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(ideas_api.ideas_db, "get_idea", fake_get_idea)
    monkeypatch.setattr(ideas_api.ideas_db, "update_status", fake_update_status)

    with caplog.at_level(logging.INFO, logger=ideas_api.log.name):
        ideas_api._draft_idea_with_crewai_background(_IDEA_ID)  # must not raise

    assert len(fake_run.calls) == 1
    payloads = _log_payloads(caplog, "idea_draft_failed")
    assert len(payloads) == 1
    assert payloads[0]["stdout_tail"] == "partial output"
    assert payloads[0]["stderr_tail"] == "boom"
    # still "drafting" -> the subprocess never reached its own terminal status -> revert
    assert fake_update_status.calls == [(_IDEA_ID, "approved")]


# --------------------------------------------------------------- non-zero exit, already classified


def test_draft_idea_failure_does_not_revert_when_already_classified(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If the subprocess itself already moved the idea to write_failed (or any
    other non-'drafting' status) before dying, the revert must be a no-op."""
    fake_claim = _fake_claim(True)
    fake_run = _fake_run(returncode=1, stderr="boom")
    fake_get_idea = _fake_get_idea("write_failed")
    fake_update_status = _fake_update_status()
    monkeypatch.setattr(ideas_api.ideas_db, "claim_idea_for_drafting", fake_claim)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(ideas_api.ideas_db, "get_idea", fake_get_idea)
    monkeypatch.setattr(ideas_api.ideas_db, "update_status", fake_update_status)

    with caplog.at_level(logging.INFO, logger=ideas_api.log.name):
        ideas_api._draft_idea_with_crewai_background(_IDEA_ID)

    assert any("idea_draft_failed" in str(r.msg) for r in caplog.records)
    assert fake_update_status.calls == []


# --------------------------------------------------------------- timeout


def test_draft_idea_timeout_logs_and_reverts_if_still_drafting(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _timeout(cmd: list[str], **_kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd, 600)

    fake_claim = _fake_claim(True)
    fake_get_idea = _fake_get_idea("drafting")
    fake_update_status = _fake_update_status()
    monkeypatch.setattr(ideas_api.ideas_db, "claim_idea_for_drafting", fake_claim)
    monkeypatch.setattr(subprocess, "run", _timeout)
    monkeypatch.setattr(ideas_api.ideas_db, "get_idea", fake_get_idea)
    monkeypatch.setattr(ideas_api.ideas_db, "update_status", fake_update_status)

    with caplog.at_level(logging.INFO, logger=ideas_api.log.name):
        ideas_api._draft_idea_with_crewai_background(_IDEA_ID)  # must not raise

    assert any("idea_draft_timeout" in str(r.msg) for r in caplog.records)
    assert fake_update_status.calls == [(_IDEA_ID, "approved")]


# --------------------------------------------------------------- generic subprocess launch error


def test_draft_idea_subprocess_launch_failure_reverts_if_still_drafting(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom(cmd: list[str], **_kwargs: Any) -> None:
        raise OSError("no such file")

    fake_claim = _fake_claim(True)
    fake_get_idea = _fake_get_idea("drafting")
    fake_update_status = _fake_update_status()
    monkeypatch.setattr(ideas_api.ideas_db, "claim_idea_for_drafting", fake_claim)
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(ideas_api.ideas_db, "get_idea", fake_get_idea)
    monkeypatch.setattr(ideas_api.ideas_db, "update_status", fake_update_status)

    with caplog.at_level(logging.INFO, logger=ideas_api.log.name):
        ideas_api._draft_idea_with_crewai_background(_IDEA_ID)  # must not raise

    assert any("idea_draft_subprocess_error" in str(r.msg) for r in caplog.records)
    assert fake_update_status.calls == [(_IDEA_ID, "approved")]
