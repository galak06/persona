"""Tests for the reel-review additions to `api/ideas_api.py`:
`_publish_reel_background` (the BackgroundTasks callback `PATCH
/ideas/{id}/status` fires on `social_done` from a `social_queued` row) and
`update_idea_status`'s reel-aware branches (Approve dispatches the
background publish rather than a blind status write; Reject clears columns
+ deletes local files rather than a blind status write).

Same convention as `test_ideas_api_draft_trigger.py`: `subprocess.run` faked
via `monkeypatch.setattr`, `lib.ideas_db` functions faked at the module
level -- no live Postgres, no FastAPI TestClient needed since these are
plain functions.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from api import ideas_api
from fastapi import BackgroundTasks

_IDEA_ID = "idea-1"


def _fake_run(returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    _run.calls = calls  # type: ignore[attr-defined]
    return _run


# --------------------------------------------------------------- _publish_reel_background


def test_publish_reel_background_invokes_publish_worker_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_run = _fake_run(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)

    ideas_api._publish_reel_background(_IDEA_ID)

    assert len(fake_run.calls) == 1
    cmd = fake_run.calls[0]
    assert "workers.worker_wp_ideas_reel_publish" in cmd
    assert cmd[-2:] == ["--idea-id", _IDEA_ID]


def test_publish_reel_background_timeout_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def _timeout_run(cmd: list[str], **_kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd, 600)

    monkeypatch.setattr(subprocess, "run", _timeout_run)
    ideas_api._publish_reel_background(_IDEA_ID)  # must not raise


# --------------------------------------------------------------- update_idea_status: Approve reel


def test_update_idea_status_approve_reel_dispatches_background_publish_not_blind_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """status='social_done' from a social_queued row must NOT go through a
    blind ideas_db.update_status write -- the row only really reaches
    social_done once the publish subprocess confirms both platforms are
    live (via set_reel_result)."""
    monkeypatch.setattr(
        ideas_api.ideas_db, "get_idea", lambda idea_id: {"id": idea_id, "status": "social_queued"}
    )

    def _fail_if_called(*_a: Any, **_k: Any) -> bool:
        raise AssertionError("update_status must not be called for the Approve-reel transition")

    monkeypatch.setattr(ideas_api.ideas_db, "update_status", _fail_if_called)

    dispatched: list[tuple[Any, tuple[Any, ...]]] = []
    bg = BackgroundTasks()
    monkeypatch.setattr(
        bg, "add_task", lambda fn, *args: dispatched.append((fn, args))
    )

    result = ideas_api.update_idea_status(
        _IDEA_ID, ideas_api.StatusBody(status="social_done"), bg
    )

    assert result == {"id": _IDEA_ID, "status": "social_queued"}
    assert len(dispatched) == 1
    assert dispatched[0][0] is ideas_api._publish_reel_background
    assert dispatched[0][1] == (_IDEA_ID,)


# --------------------------------------------------------------- update_idea_status: Reject reel


def test_update_idea_status_reject_reel_clears_columns_and_deletes_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ig_file = tmp_path / "idea-1_ig.mp4"
    fb_file = tmp_path / "idea-1_fb.mp4"
    ig_file.write_bytes(b"ig")
    fb_file.write_bytes(b"fb")

    monkeypatch.setattr(
        ideas_api.ideas_db,
        "get_idea",
        lambda idea_id: {
            "id": idea_id,
            "status": "social_queued",
            "reel_ig_video_path": str(ig_file),
            "reel_fb_video_path": str(fb_file),
        },
    )
    reject_calls: list[str] = []
    monkeypatch.setattr(
        ideas_api.ideas_db, "reject_reel", lambda idea_id: reject_calls.append(idea_id) or True
    )

    bg = BackgroundTasks()
    result = ideas_api.update_idea_status(
        _IDEA_ID, ideas_api.StatusBody(status="wp_published"), bg
    )

    assert result == {"id": _IDEA_ID, "status": "wp_published"}
    assert reject_calls == [_IDEA_ID]
    assert not ig_file.exists()
    assert not fb_file.exists()


def test_update_idea_status_wp_published_from_non_queued_row_is_a_blind_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PATCH to 'wp_published' from anything OTHER than social_queued
    (e.g. a hypothetical future caller) must fall through to the ordinary
    blind status write, not be mistaken for a reel rejection."""
    monkeypatch.setattr(
        ideas_api.ideas_db, "get_idea", lambda idea_id: {"id": idea_id, "status": "wp_draft"}
    )
    reject_calls: list[str] = []
    monkeypatch.setattr(
        ideas_api.ideas_db, "reject_reel", lambda idea_id: reject_calls.append(idea_id) or True
    )
    update_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ideas_api.ideas_db,
        "update_status",
        lambda idea_id, status: update_calls.append((idea_id, status)) or True,
    )

    bg = BackgroundTasks()
    ideas_api.update_idea_status(_IDEA_ID, ideas_api.StatusBody(status="wp_published"), bg)

    assert reject_calls == []
    assert update_calls == [(_IDEA_ID, "wp_published")]
