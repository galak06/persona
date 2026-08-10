"""Tests for `api/social_posts_api.py` -- the FB+IG post review routes.

Same convention as `test_ideas_api_reel_transitions.py`: `subprocess.run`
faked via `monkeypatch.setattr`, DB modules faked at the module level -- no
live Postgres, no FastAPI TestClient needed since these are plain functions.
"""
# ruff: noqa: S101

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from api import social_posts_api
from fastapi import BackgroundTasks, HTTPException

_IDEA_ID = "idea-1"


def _fake_run(returncode: int = 0) -> Any:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="")

    _run.calls = calls  # type: ignore[attr-defined]
    return _run


def _queued_idea(**overrides: Any) -> dict[str, Any]:
    idea = {
        "id": _IDEA_ID,
        "topic": "Bone broth",
        "wp_url": "https://x.com/p",
        "social_post_status": "queued",
        "social_post_fb_caption": "fb",
        "social_post_ig_caption": "ig",
        "social_post_image_path": "state/social_posts_pending/idea-1.jpg",
        "social_post_image_alt": "alt",
        "social_post_source": "gemini",
        "social_post_validation_flags": None,
        "fb_page_post_url": None,
        "ig_post_url": None,
        "social_post_ig_due_at": None,
    }
    idea.update(overrides)
    return idea


# ------------------------------------------------------------ approve


def test_approve_dispatches_fb_publish_background(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(social_posts_api.ideas_db, "get_idea", lambda _id: _queued_idea())
    fake_run = _fake_run(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)

    tasks = BackgroundTasks()
    result = social_posts_api.approve_social_post(_IDEA_ID, tasks)
    assert result == {"id": _IDEA_ID, "status": "queued"}

    # Run the queued background task synchronously to assert the dispatch.
    for task in tasks.tasks:
        task.func(*task.args, **task.kwargs)
    assert len(fake_run.calls) == 1
    cmd = fake_run.calls[0]
    assert "workers.worker_wp_ideas_social_post" in cmd
    assert "--platform" in cmd
    assert cmd[cmd.index("--platform") + 1] == "fb"


def test_approve_409_when_not_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        social_posts_api.ideas_db,
        "get_idea",
        lambda _id: _queued_idea(social_post_status="fb_published"),
    )
    with pytest.raises(HTTPException) as exc:
        social_posts_api.approve_social_post(_IDEA_ID, BackgroundTasks())
    assert exc.value.status_code == 409


def test_approve_404_on_missing_idea(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(social_posts_api.ideas_db, "get_idea", lambda _id: None)
    with pytest.raises(HTTPException) as exc:
        social_posts_api.approve_social_post(_IDEA_ID, BackgroundTasks())
    assert exc.value.status_code == 404


# ------------------------------------------------------------ reject


def test_reject_terminal_and_deletes_image(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    image = tmp_path / "state" / "social_posts_pending" / "idea-1.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"jpeg")
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    monkeypatch.setattr(social_posts_api.ideas_db, "get_idea", lambda _id: _queued_idea())
    rejected: list[str] = []
    monkeypatch.setattr(
        social_posts_api.social_post_db, "reject", lambda i: rejected.append(i) or True
    )

    result = social_posts_api.reject_social_post(_IDEA_ID)
    assert result == {"id": _IDEA_ID, "status": "rejected"}
    assert rejected == [_IDEA_ID]
    assert not image.exists()


def test_reject_409_when_db_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(social_posts_api.ideas_db, "get_idea", lambda _id: _queued_idea())
    monkeypatch.setattr(social_posts_api.social_post_db, "reject", lambda _i: False)
    with pytest.raises(HTTPException) as exc:
        social_posts_api.reject_social_post(_IDEA_ID)
    assert exc.value.status_code == 409


# ------------------------------------------------------------ list/image


def test_list_social_posts_filters_by_track_status(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _queued_idea(),
        _queued_idea(id="idea-2", social_post_status="rejected"),
        _queued_idea(id="idea-3", social_post_status=None),
    ]
    monkeypatch.setattr(social_posts_api.ideas_db, "list_ideas", lambda **_kw: rows)
    resp = social_posts_api.list_social_posts(status="queued", brand_id=None, limit=100)
    assert resp.total == 1
    assert resp.posts[0].id == _IDEA_ID
    assert resp.posts[0].fb_caption == "fb"


def test_list_social_posts_rejects_unknown_status(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HTTPException) as exc:
        social_posts_api.list_social_posts(status="nope", brand_id=None, limit=100)
    assert exc.value.status_code == 422


def test_get_image_404_when_not_composed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        social_posts_api.ideas_db,
        "get_idea",
        lambda _id: _queued_idea(social_post_image_path=None),
    )
    with pytest.raises(HTTPException) as exc:
        social_posts_api.get_social_post_image(_IDEA_ID)
    assert exc.value.status_code == 404
