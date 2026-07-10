# pyright: reportMissingImports=false
"""Tests for the engagements DB layer + its read API.

Repository tests are real integration tests against a live local Postgres,
following `test_db.py`'s skipif pattern — they run when one is reachable at
`DATABASE_URL` (or `lib.db_pool`'s local dev default) and skip cleanly
otherwise. CI provides a `postgres:16` service container with `DATABASE_URL`
set, so they run for real there. This replaces the previous version of this
file, which exercised a SQLite `connect()`/`migrate()` API that no longer
exists now that engagements_db runs on Postgres via `lib/db.py`.

The API-handler test is a pure unit test (module-level helpers monkeypatched,
no DB, no ASGI app) and always runs.
"""
# ruff: noqa: S101

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from lib import db
from lib.engagements_db.repository import EngagementsRepository

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def _postgres_reachable() -> bool:
    """Best-effort connectivity probe, used to skip DB tests when none is available."""
    try:
        return db.health_check()
    except Exception:
        return False


_PG_AVAILABLE = _postgres_reachable()
_SKIP_REASON = "No reachable Postgres at DATABASE_URL (or lib.db_pool's local default)"
requires_postgres = pytest.mark.skipif(not _PG_AVAILABLE, reason=_SKIP_REASON)


@pytest.fixture
def repo() -> Iterator[EngagementsRepository]:
    """Apply schema.sql (idempotent), yield a fresh repository, then truncate."""
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield EngagementsRepository()
    finally:
        db.execute("TRUNCATE TABLE engagements")


@requires_postgres
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


@requires_postgres
def test_record_is_idempotent_by_ref(repo: EngagementsRepository) -> None:
    id1 = repo.record({"platform": "facebook", "kind": "comment", "ref": "p1", "status": "failed"})
    id2 = repo.record({"platform": "facebook", "kind": "comment", "ref": "p1", "status": "posted"})
    assert id1 == id2
    rows = repo.list_engagements()
    assert len(rows) == 1  # upsert, not duplicate
    assert rows[0]["status"] == "posted"  # status updated in place


@requires_postgres
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


@requires_postgres
def test_list_engagements_respects_limit_and_ordering(repo: EngagementsRepository) -> None:
    repo.record(
        {
            "platform": "facebook",
            "kind": "comment",
            "ref": "old",
            "posted_at": "2026-01-01T00:00:00Z",
        }
    )
    repo.record(
        {
            "platform": "facebook",
            "kind": "comment",
            "ref": "new",
            "posted_at": "2026-06-01T00:00:00Z",
        }
    )
    rows = repo.list_engagements(limit=1)
    assert len(rows) == 1
    assert rows[0]["posted_at"] == "2026-06-01T00:00:00Z"  # most-recent-first


@requires_postgres
def test_posted_comment_post_ids_is_the_dup_guard(repo: EngagementsRepository) -> None:
    repo.record({"platform": "instagram", "kind": "comment", "ref": "p1", "status": "posted"})
    repo.record({"platform": "instagram", "kind": "comment", "ref": "p2", "status": "failed"})
    got = repo.posted_comment_post_ids("instagram", ["p1", "p2", "p3"])
    assert got == {"p1"}  # only the POSTED one; failed/absent don't block
    # platform-scoped: same ref on FB doesn't count for IG
    assert repo.posted_comment_post_ids("facebook", ["p1"]) == set()


@requires_postgres
def test_get_returns_row_or_none(repo: EngagementsRepository) -> None:
    eid = repo.record({"platform": "facebook", "kind": "comment", "ref": "p1"})
    row = repo.get(eid)
    assert row is not None
    assert row["id"] == eid
    assert repo.get("no-such-id") is None


def test_record_requires_platform_and_kind() -> None:
    """Validation raises before any DB call — doesn't need a live Postgres."""
    repo = EngagementsRepository()
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
    monkeypatch.setattr(engagements_api.engagements_db, "list_engagements", lambda **_: fake_rows)
    monkeypatch.setattr(engagements_api.engagements_db, "counts", lambda: {"facebook:comment": 1})

    resp = engagements_api.list_engagements_endpoint()
    assert resp.total == 1
    assert resp.engagements[0].platform == "facebook"
    assert resp.engagements[0].content == "hi"
    assert resp.counts == {"facebook:comment": 1}
