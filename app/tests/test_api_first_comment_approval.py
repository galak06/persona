# pyright: reportMissingImports=false
"""Tests for `api/groups_api.py`
(`POST /api/v1/facebook/groups/{group_name}/approve-first-comment`).

Real-Postgres + TestClient round trips over the full app, following
`test_api_brand_flows.py`'s live-test convention (skip cleanly when no
Postgres is reachable; CI provides one). The endpoint is the user's one-time
per-group approval for the fb-engager first-comment gate: it resolves the
group by `group_name` (`groups_db.get_by_name`), stamps
`first_comment_approved_at` on its `group_url`, and returns the refreshed
group. Approving a never-flagged group is deliberately allowed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from lib import db, groups_db

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"

# A name with spaces on purpose: the frontend calls the endpoint with
# encodeURIComponent(group_name), so the path round trip must decode back
# to the exact stored group_name.
_GROUP_NAME = "Homemade Pet Treats"
_GROUP_URL = "https://www.facebook.com/groups/219924639809303/"


def _postgres_reachable() -> bool:
    try:
        return db.health_check()
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_reachable(),
    reason="No reachable Postgres at DATABASE_URL (or lib.db_pool's local default)",
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Schema applied (idempotent), one joined group seeded under a throwaway
    brand, TestClient over the real app; truncate what this module touched."""
    from api.approval_api import app

    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    monkeypatch.setenv("BRAND_DIR", "/tmp/test-first-comment-approval-brand")
    groups_db.save_all([{"group_name": _GROUP_NAME, "group_url": _GROUP_URL, "status": "joined"}])
    try:
        yield TestClient(app)
    finally:
        db.execute("TRUNCATE TABLE fb_groups, brands CASCADE")


def _approve(client: TestClient, name: str) -> httpx.Response:
    return client.post(f"/api/v1/facebook/groups/{quote(name, safe='')}/approve-first-comment")


@requires_postgres
def test_approve_first_comment_returns_and_persists_the_stamp(client: TestClient) -> None:
    resp = _approve(client, _GROUP_NAME)
    assert resp.status_code == 200
    body = resp.json()
    assert body["group_name"] == _GROUP_NAME
    assert body["group_url"] == _GROUP_URL
    approved_at = body["first_comment_approved_at"]
    assert approved_at  # non-empty ISO stamp

    # Persisted, not just echoed: a fresh read sees the same stamp.
    stored = groups_db.get_by_name(_GROUP_NAME)
    assert stored is not None
    assert stored["first_comment_approved_at"] == approved_at


@requires_postgres
def test_approve_first_comment_allows_pre_approval_of_unflagged_group(
    client: TestClient,
) -> None:
    """No flagged-state precondition: pre-approving a group the engager has
    not reached yet is harmless (its first comment simply posts inline)."""
    stored = groups_db.get_by_name(_GROUP_NAME)
    assert stored is not None
    assert "first_comment_flagged_at" not in stored  # never flagged

    resp = _approve(client, _GROUP_NAME)
    assert resp.status_code == 200
    assert resp.json()["first_comment_approved_at"]


@requires_postgres
def test_approve_first_comment_unknown_group_404s(client: TestClient) -> None:
    resp = _approve(client, "No Such Group")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]
    # And the seeded group was not touched by the failed call.
    stored = groups_db.get_by_name(_GROUP_NAME)
    assert stored is not None
    assert "first_comment_approved_at" not in stored
