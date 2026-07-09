# pyright: reportMissingImports=false
"""Route-level test: affiliate_products surfaces through GET /recipes.

Calls ``api.recipes_api.list_recipes`` against a seeded temp DB (read-only
connection monkeypatched in) and asserts matched affiliate products round-trip
into the typed ``AffiliateProduct`` response model.
"""
# ruff: noqa: S101

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

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
    RecipeRepository(conn).upsert_recipe(
        RecipeRow(
            name="Mat Cookies",
            content_hash="h1",
            affiliate_products=[
                {"key": "mat", "asin": "B00ABCDE12", "display": "Baking Mat"}
            ],
        )
    )
    conn.close()
    monkeypatch.setattr(recipes_api, "_open_readonly", lambda: _readonly(path))
    return path


def test_affiliate_products_in_summary(seeded_db: Path) -> None:
    res = recipes_api.list_recipes(
        status=None, dog_safe=None, season=None, content_status=None
    )
    assert res.total == 1
    products = res.recipes[0].affiliate_products
    assert len(products) == 1
    assert products[0].key == "mat"
    assert products[0].asin == "B00ABCDE12"
    assert products[0].display == "Baking Mat"
