"""Tests for `api/social_posts_api.py` -- the FB+IG post review routes.

Same convention as `test_ideas_api_reel_transitions.py`: `subprocess.run`
faked via `monkeypatch.setattr`, DB modules faked at the module level -- no
live Postgres, no FastAPI TestClient needed since these are plain functions.
"""
# ruff: noqa: S101

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from typing import Any

import pytest
from api import social_posts_api
from fastapi import HTTPException

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
        "social_post_fb_due_at": None,
        "social_post_ig_due_at": None,
        "brand_id": "dogfoodandfun",
    }
    idea.update(overrides)
    return idea


# ------------------------------------------------------------ approve


class _FrozenDatetime(datetime):
    """`next_free_slot` is fed `datetime.now(UTC)`, so the expected slot moves
    with the wall clock unless it is pinned."""

    @classmethod
    def now(cls, tz: object = None) -> datetime:  # noqa: ARG003 - signature parity
        return datetime(2026, 8, 11, 20, tzinfo=UTC)


def test_approve_schedules_and_publishes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Approve claims a slot; no publish subprocess may run."""
    monkeypatch.setattr(social_posts_api, "datetime", _FrozenDatetime)
    monkeypatch.setattr(social_posts_api.ideas_db, "get_idea", lambda _id: _queued_idea())
    monkeypatch.setattr(
        social_posts_api.social_post_db,
        "last_scheduled_fb_slot",
        lambda **_kw: datetime(2026, 8, 11, 13, tzinfo=UTC),
    )
    scheduled: list[tuple[str, datetime]] = []
    monkeypatch.setattr(
        social_posts_api.social_post_db,
        "schedule_fb",
        lambda i, *, due_at: scheduled.append((i, due_at)) or True,
    )
    fake_run = _fake_run(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = social_posts_api.approve_social_post(_IDEA_ID)

    assert result["status"] == "scheduled"
    assert len(scheduled) == 1
    # One full gap after the brand's last claimed slot.
    assert scheduled[0][1] == datetime(2026, 8, 12, 13, tzinfo=UTC)
    assert result["fb_due_at"] == "2026-08-12T13:00:00+00:00"
    assert fake_run.calls == []  # nothing published


def test_approve_first_post_takes_next_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(social_posts_api.ideas_db, "get_idea", lambda _id: _queued_idea())
    monkeypatch.setattr(
        social_posts_api.social_post_db, "last_scheduled_fb_slot", lambda **_kw: None
    )
    captured: list[datetime] = []
    monkeypatch.setattr(
        social_posts_api.social_post_db,
        "schedule_fb",
        lambda _i, *, due_at: captured.append(due_at) or True,
    )

    social_posts_api.approve_social_post(_IDEA_ID)

    assert len(captured) == 1
    # Always a future preferred-hour window, never "now".
    assert captured[0] > datetime.now(UTC)
    assert captured[0].hour == social_posts_api.social_post_slots.DEFAULT_PREFERRED_HOUR_UTC


def test_approve_409_when_not_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        social_posts_api.ideas_db,
        "get_idea",
        lambda _id: _queued_idea(social_post_status="scheduled"),
    )
    with pytest.raises(HTTPException) as exc:
        social_posts_api.approve_social_post(_IDEA_ID)
    assert exc.value.status_code == 409


def test_approve_404_on_missing_idea(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(social_posts_api.ideas_db, "get_idea", lambda _id: None)
    with pytest.raises(HTTPException) as exc:
        social_posts_api.approve_social_post(_IDEA_ID)
    assert exc.value.status_code == 404


def test_unschedule_returns_to_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(social_posts_api.social_post_db, "unschedule_fb", lambda _i: True)
    assert social_posts_api.unschedule_social_post(_IDEA_ID) == {
        "id": _IDEA_ID,
        "status": "queued",
    }


def test_unschedule_409_when_not_scheduled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(social_posts_api.social_post_db, "unschedule_fb", lambda _i: False)
    with pytest.raises(HTTPException) as exc:
        social_posts_api.unschedule_social_post(_IDEA_ID)
    assert exc.value.status_code == 409


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


def test_a_post_being_re_imaged_stays_in_the_review_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry claims the row 'queued' -> 'composing' for the ~60s it runs. If
    the listing dropped it the card would vanish mid-retry and come back, which
    reads as a bug and loses the reviewer's place."""
    rows = [_queued_idea(social_post_status="composing")]
    monkeypatch.setattr(social_posts_api.ideas_db, "list_ideas", lambda **_kw: rows)

    resp = social_posts_api.list_social_posts(status="queued", brand_id=None, limit=100)

    assert resp.total == 1
    assert resp.posts[0].regenerating is True


def test_a_first_composition_is_not_in_the_review_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'composing' with NO image is a post being made for the first time: no
    captions, no image, nothing to review. The image path is what separates the
    two, and it must keep separating them."""
    rows = [_queued_idea(social_post_status="composing", social_post_image_path=None)]
    monkeypatch.setattr(social_posts_api.ideas_db, "list_ideas", lambda **_kw: rows)

    resp = social_posts_api.list_social_posts(status="queued", brand_id=None, limit=100)

    assert resp.total == 0


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
