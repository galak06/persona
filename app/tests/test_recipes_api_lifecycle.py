# pyright: reportMissingImports=false
"""Route-level tests for the lifecycle endpoints: approve/reject (P5),
analytics (P10), and the content_status filter on GET /recipes."""
# ruff: noqa: S101

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

_RP = Path(__file__).resolve().parent.parent / "recipe-publisher"
if str(_RP) not in sys.path:
    sys.path.insert(0, str(_RP))

from api import recipes_api
from recipe_db.db import connect, migrate
from recipe_db.models import ContentStatus, RecipeRow
from recipe_db.repository import RecipeRepository


def _readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "recipes.db"
    conn = connect(path)
    migrate(conn)
    repo = RecipeRepository(conn)
    repo.upsert_recipe(RecipeRow(name="Pending One", content_hash="x1"))
    repo.set_content_status("pending-one", ContentStatus.PENDING)
    repo.upsert_recipe(RecipeRow(name="Approved Two", content_hash="x2"))
    repo.set_content_status("approved-two", ContentStatus.APPROVED)
    repo.set_publish_results(
        "approved-two", [{"platform": "ig", "status": "dry_run"}]
    )
    conn.close()
    monkeypatch.setattr(recipes_api, "_open_readonly", lambda: _readonly(path))
    monkeypatch.setattr(recipes_api, "_open_writable", lambda: connect(path))
    return path


def test_approve_endpoint(seeded: Path) -> None:
    res = recipes_api.approve_recipe("pending-one")
    assert res.content_status == "approved"
    listing = recipes_api.list_recipes(
        status=None, dog_safe=None, season=None, content_status="approved"
    )
    assert "pending-one" in {r.id for r in listing.recipes}


def test_reject_non_pending_is_400(seeded: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        recipes_api.reject_recipe("approved-two")  # APPROVED, not PENDING
    assert exc.value.status_code == 400


def test_analytics_endpoint(seeded: Path) -> None:
    res = recipes_api.recipes_analytics()
    assert res.attempts >= 1
    assert res.by_status.get("dry_run", 0) >= 1


def test_content_status_filter(seeded: Path) -> None:
    res = recipes_api.list_recipes(
        status=None, dog_safe=None, season=None, content_status="pending"
    )
    assert {r.id for r in res.recipes} == {"pending-one"}
