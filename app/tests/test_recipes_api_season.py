# pyright: reportMissingImports=false
"""Route-level tests for the GET /recipes season filter.

Calls the ``api.recipes_api.list_recipes`` handler directly against a seeded
temp DB (read-only connection monkeypatched in), exercising season
normalization, the in-season filter, and the 400 on an unknown season —
without standing up the full ASGI app.
"""
# ruff: noqa: S101

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

# ``recipe_db`` lives under the hyphenated recipe-publisher dir.
_RP = Path(__file__).resolve().parent.parent / "recipe-publisher"
if str(_RP) not in sys.path:
    sys.path.insert(0, str(_RP))

from api import recipes_api
from recipe_db.db import connect, migrate
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository


def _readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "recipes.db"
    conn = connect(path)
    migrate(conn)
    repo = RecipeRepository(conn)
    repo.upsert_recipe(
        RecipeRow(
            name="Pumpkin Spice Treats",
            tags=["pumpkin"],
            content_hash="h1",
            season_tags=["fall"],
        )
    )
    repo.upsert_recipe(
        RecipeRow(
            name="Frozen Watermelon Pupsicles",
            tags=["frozen"],
            content_hash="h2",
            season_tags=["summer"],
        )
    )
    # No season_tags -> inferred all-season (no seasonal keyword) -> always shown.
    repo.upsert_recipe(
        RecipeRow(name="Everyday Chicken Biscuits", tags=["chicken"], content_hash="h3")
    )
    conn.close()
    monkeypatch.setattr(recipes_api, "_open_readonly", lambda: _readonly(path))
    return path


def test_season_filter_selects_in_season(seeded_db: Path) -> None:
    res = recipes_api.list_recipes(
        status=None, dog_safe=None, season="fall", content_status=None
    )
    names = {r.name for r in res.recipes}
    assert names == {"Pumpkin Spice Treats", "Everyday Chicken Biscuits"}


def test_no_season_filter_returns_all(seeded_db: Path) -> None:
    res = recipes_api.list_recipes(
        status=None, dog_safe=None, season=None, content_status=None
    )
    assert res.total == 3


def test_unknown_season_is_400(seeded_db: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        recipes_api.list_recipes(
            status=None, dog_safe=None, season="monsoon", content_status=None
        )
    assert exc.value.status_code == 400
