"""Tests for `workers/worker_wp_ideas.py::_do_one` -- the cron safety-net
worker that drafts approved content ideas via `scripts.crewai_content_pipeline`,
invoked as a subprocess. `_do_one` first atomically claims the idea via
`ideas_db.claim_idea_for_drafting` (the concurrency guard against a parallel
API-triggered background task claiming the same idea); only on a won claim
does it spawn the subprocess. Following this project's established convention
for subprocess-spawning code (see app/tests/test_task_worker.py), the
subprocess call is faked via `monkeypatch.setattr(subprocess, "run", ...)`
rather than actually spawning a process or importing the real pipeline
module, and `lib.ideas_db.claim_idea_for_drafting` / `get_idea` /
`update_status` are faked at the module level so no live Postgres is
required.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

import pytest
from workers import worker_wp_ideas as w

_IDEA = {"id": "idea-1", "topic": "Best dog treats"}


def _fake_run(returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    _run.calls = calls  # type: ignore[attr-defined]
    return _run


def _fake_claim(result: bool = True) -> Any:
    calls: list[str] = []

    def _claim(idea_id: str) -> bool:
        calls.append(idea_id)
        return result

    _claim.calls = calls  # type: ignore[attr-defined]
    return _claim


def _fake_get_idea(status: str | None) -> Any:
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


def test_do_one_dry_run_returns_without_invoking_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dry_run short-circuits before claim_idea_for_drafting is even called."""
    fake_run = _fake_run()
    fake_claim = _fake_claim(True)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(w.ideas_db, "claim_idea_for_drafting", fake_claim)

    result = w._do_one(_IDEA, dry_run=True)

    assert result == "dry_run"
    assert fake_run.calls == []
    assert fake_claim.calls == []


def test_do_one_success_returns_drafted(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_run = _fake_run(returncode=0)
    fake_claim = _fake_claim(True)
    fake_get_idea = _fake_get_idea("wp_draft")
    fake_update_status = _fake_update_status()
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(w.ideas_db, "claim_idea_for_drafting", fake_claim)
    monkeypatch.setattr(w.ideas_db, "get_idea", fake_get_idea)
    monkeypatch.setattr(w.ideas_db, "update_status", fake_update_status)

    result = w._do_one(_IDEA, dry_run=False)

    assert result == "drafted"
    assert fake_claim.calls == ["idea-1"]
    assert len(fake_run.calls) == 1
    cmd = fake_run.calls[0]
    assert "scripts.crewai_content_pipeline" in cmd
    assert cmd[-2:] == ["--idea-id", "idea-1"]
    # success path never reverts
    assert fake_get_idea.calls == []
    assert fake_update_status.calls == []


def test_do_one_nonzero_exit_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_run = _fake_run(returncode=1, stderr="boom")
    fake_claim = _fake_claim(True)
    fake_get_idea = _fake_get_idea("write_failed")
    fake_update_status = _fake_update_status()
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(w.ideas_db, "claim_idea_for_drafting", fake_claim)
    monkeypatch.setattr(w.ideas_db, "get_idea", fake_get_idea)
    monkeypatch.setattr(w.ideas_db, "update_status", fake_update_status)

    result = w._do_one(_IDEA, dry_run=False)

    assert result == "error"
    # already classified by the subprocess itself -> no revert
    assert fake_update_status.calls == []


def test_do_one_timeout_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _timeout(cmd: list[str], **_kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd, 600)

    fake_claim = _fake_claim(True)
    fake_get_idea = _fake_get_idea("drafting")
    fake_update_status = _fake_update_status()
    monkeypatch.setattr(subprocess, "run", _timeout)
    monkeypatch.setattr(w.ideas_db, "claim_idea_for_drafting", fake_claim)
    monkeypatch.setattr(w.ideas_db, "get_idea", fake_get_idea)
    monkeypatch.setattr(w.ideas_db, "update_status", fake_update_status)

    result = w._do_one(_IDEA, dry_run=False)

    assert result == "error"
    assert fake_update_status.calls == [("idea-1", "approved")]


def test_do_one_subprocess_launch_failure_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("no such file")

    fake_claim = _fake_claim(True)
    fake_get_idea = _fake_get_idea("drafting")
    fake_update_status = _fake_update_status()
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(w.ideas_db, "claim_idea_for_drafting", fake_claim)
    monkeypatch.setattr(w.ideas_db, "get_idea", fake_get_idea)
    monkeypatch.setattr(w.ideas_db, "update_status", fake_update_status)

    result = w._do_one(_IDEA, dry_run=False)

    assert result == "error"
    assert fake_update_status.calls == [("idea-1", "approved")]


# --------------------------------------------------------------- claim lost (not approved)


def test_do_one_skipped_when_claim_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """If claim_idea_for_drafting can't win (idea no longer status='approved' --
    e.g. the API's background task already claimed it), the subprocess must
    never be spawned and the outcome must be 'skipped', not 'error'."""
    fake_run = _fake_run(returncode=0)
    fake_claim = _fake_claim(False)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(w.ideas_db, "claim_idea_for_drafting", fake_claim)

    result = w._do_one(_IDEA, dry_run=False)

    assert result == "skipped"
    assert fake_claim.calls == ["idea-1"]
    assert fake_run.calls == []


# --------------------------------------------------------------- revert on stuck "drafting"


def test_do_one_failure_reverts_stuck_drafting_claim(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake_run = _fake_run(returncode=1, stderr="boom")
    fake_claim = _fake_claim(True)
    fake_get_idea = _fake_get_idea("drafting")
    fake_update_status = _fake_update_status()
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(w.ideas_db, "claim_idea_for_drafting", fake_claim)
    monkeypatch.setattr(w.ideas_db, "get_idea", fake_get_idea)
    monkeypatch.setattr(w.ideas_db, "update_status", fake_update_status)

    with caplog.at_level(logging.INFO, logger=w.log.name):
        result = w._do_one(_IDEA, dry_run=False)

    assert result == "error"
    assert fake_get_idea.calls == ["idea-1"]
    assert fake_update_status.calls == [("idea-1", "approved")]
    assert any("idea_draft_reverted_to_approved" in str(r.msg) for r in caplog.records)


# --------------------------------------------------------------- json.dumps log regression (bug fix)


def test_do_one_failure_log_is_valid_json_with_embedded_quotes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Regression test for the bug this phase fixed: hand-rolled pseudo-JSON
    log strings broke (produced invalid JSON) whenever a real traceback/stderr
    contained a literal `"` character. The log lines are now built with
    `json.dumps(...)`, which must escape embedded quotes correctly so the
    resulting log message is still valid, parseable JSON."""
    quote_bearing_stderr = 'Traceback: KeyError: "topic" not found in idea dict'
    fake_run = _fake_run(returncode=1, stderr=quote_bearing_stderr)
    fake_claim = _fake_claim(True)
    fake_get_idea = _fake_get_idea("write_failed")
    fake_update_status = _fake_update_status()
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(w.ideas_db, "claim_idea_for_drafting", fake_claim)
    monkeypatch.setattr(w.ideas_db, "get_idea", fake_get_idea)
    monkeypatch.setattr(w.ideas_db, "update_status", fake_update_status)

    with caplog.at_level(logging.INFO, logger=w.log.name):
        result = w._do_one(_IDEA, dry_run=False)

    assert result == "error"
    failure_records = [r for r in caplog.records if "idea_draft_failed" in str(r.msg)]
    assert len(failure_records) == 1
    # must not raise -- this is the direct regression check
    payload = json.loads(failure_records[0].msg)
    assert payload["event"] == "idea_draft_failed"
    assert payload["stderr"] == quote_bearing_stderr
