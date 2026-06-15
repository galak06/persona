# pyright: reportMissingImports=false
"""Tests for the engagements DB layer + its read API.

Repository tests run against a temp engagements.db (connect+migrate). The API
test calls the ``api.engagements_api`` handler directly with the module-level
helpers monkeypatched — no ASGI app, no real DB.
"""
# ruff: noqa: S101

from __future__ import annotations

from pathlib import Path

import pytest

from lib.engagements_db.db import connect, migrate
from lib.engagements_db.repository import EngagementsRepository


@pytest.fixture
def repo(tmp_path: Path) -> EngagementsRepository:
    conn = connect(tmp_path / "engagements.db")
    migrate(conn)
    return EngagementsRepository(conn)


def test_record_and_list_round_trip(repo: EngagementsRepository) -> None:
    repo.record(
        {
            "platform": "facebook",
            "kind": "comment",
            "target_name": "All About Dog Food",
            "target_url": "https://fb.com/groups/x/posts/1",
            "content": "We tried this with Nalla — how about you?",
            "ref": "post1",
        }
    )
    rows = repo.list_engagements()
    assert len(rows) == 1
    assert rows[0]["platform"] == "facebook"
    assert rows[0]["kind"] == "comment"
    assert rows[0]["status"] == "posted"
    assert rows[0]["target_name"] == "All About Dog Food"
    assert rows[0]["posted_at"]  # auto-stamped


def test_record_is_idempotent_by_ref(repo: EngagementsRepository) -> None:
    id1 = repo.record({"platform": "facebook", "kind": "comment", "ref": "p1", "status": "failed"})
    id2 = repo.record({"platform": "facebook", "kind": "comment", "ref": "p1", "status": "posted"})
    assert id1 == id2
    rows = repo.list_engagements()
    assert len(rows) == 1  # upsert, not duplicate
    assert rows[0]["status"] == "posted"  # status updated in place


def test_filters_and_counts(repo: EngagementsRepository) -> None:
    repo.record({"platform": "facebook", "kind": "comment", "ref": "a", "status": "posted"})
    repo.record({"platform": "facebook", "kind": "link_post", "ref": "b", "status": "posted"})
    repo.record({"platform": "instagram", "kind": "reel", "ref": "c", "status": "posted"})
    repo.record({"platform": "facebook", "kind": "comment", "ref": "d", "status": "failed"})

    assert len(repo.list_engagements(platform="facebook")) == 3
    assert len(repo.list_engagements(platform="facebook", kind="comment")) == 2
    assert len(repo.list_engagements(status="failed")) == 1
    # counts are posted-only
    assert repo.counts() == {
        "facebook:comment": 1,
        "facebook:link_post": 1,
        "instagram:reel": 1,
    }


def test_posted_comment_post_ids_is_the_dup_guard(repo: EngagementsRepository) -> None:
    repo.record(
        {"platform": "instagram", "kind": "comment", "ref": "p1", "status": "posted"}
    )
    repo.record(
        {"platform": "instagram", "kind": "comment", "ref": "p2", "status": "failed"}
    )
    got = repo.posted_comment_post_ids("instagram", ["p1", "p2", "p3"])
    assert got == {"p1"}  # only the POSTED one; failed/absent don't block
    # platform-scoped: same ref on FB doesn't count for IG
    assert repo.posted_comment_post_ids("facebook", ["p1"]) == set()


def test_record_requires_platform_and_kind(repo: EngagementsRepository) -> None:
    with pytest.raises(ValueError):
        repo.record({"platform": "facebook"})
    with pytest.raises(ValueError):
        repo.record({"kind": "comment"})


def test_api_handler_returns_response(monkeypatch: pytest.MonkeyPatch) -> None:
    from api import engagements_api

    fake_rows = [
        {
            "id": "facebook:comment:p1",
            "platform": "facebook",
            "kind": "comment",
            "status": "posted",
            "target_name": "Dogs",
            "target_url": "u",
            "permalink": "",
            "content": "hi",
            "source_ref": "",
            "error": "",
            "posted_at": "2026-06-14T00:00:00Z",
        }
    ]
    monkeypatch.setattr(
        engagements_api.engagements_db, "list_engagements", lambda **_: fake_rows
    )
    monkeypatch.setattr(
        engagements_api.engagements_db, "counts", lambda: {"facebook:comment": 1}
    )

    resp = engagements_api.list_engagements_endpoint()
    assert resp.total == 1
    assert resp.engagements[0].platform == "facebook"
    assert resp.engagements[0].content == "hi"
    assert resp.counts == {"facebook:comment": 1}
