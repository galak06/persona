# pyright: reportMissingImports=false
"""Service-level tests for the affiliate-matching phase (data-layer E2E)."""
# ruff: noqa: S101

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from lib.recipe_products.catalog import RecipeCatalog, load_catalog
from pipeline.affiliate_matching import AffiliateMatcher
from recipe_db.db import connect, migrate
from recipe_db.models import Ingredient, RecipeRow
from recipe_db.repository import RecipeRepository

_CATALOG = {
    "categories": {
        "baking": [
            {
                "key": "silicone-mat",
                "asin": "B00ABCDE12",
                "display": "Silicone Baking Mat",
                "blurb": "Non-stick mat.",
            }
        ],
        "storage": [
            {
                "key": "treat-jar",
                "asin": "B00ZZZZ123",
                "display": "Treat Jar",
                "blurb": "Airtight jar.",
            }
        ],
    },
    "recipe_type_map": {"pumpkin": ["baking"], "_default": ["storage"]},
    "recipe_overrides": {},
}


def _catalog(tmp_path: Path) -> RecipeCatalog:
    path = tmp_path / "recipe_products.json"
    path.write_text(json.dumps(_CATALOG), encoding="utf-8")
    return load_catalog(path)


def _seed(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    repo = RecipeRepository(conn)
    # Title has no "pumpkin" — match must come from the *ingredient*.
    repo.upsert_recipe(
        RecipeRow(
            name="Autumn Biscuits",
            ingredients=[Ingredient(item="pumpkin puree")],
            content_hash="h1",
        )
    )
    repo.upsert_recipe(
        RecipeRow(
            name="Berry Pupsicles",
            ingredients=[Ingredient(item="blueberries")],
            content_hash="h2",
        )
    )
    return conn, repo


def test_matches_and_persists(tmp_path: Path) -> None:
    conn, repo = _seed(tmp_path)
    try:
        report = AffiliateMatcher(repo, _catalog(tmp_path), limit=3).run(persist=True)
        assert report.total == 2
        assert report.recipes_with_products == 2
        assert report.matched == 2
        # ingredient-driven match: "pumpkin puree" -> baking -> silicone-mat.
        autumn = repo.get_recipe("autumn-biscuits")
        assert autumn is not None
        assert autumn.affiliate_products == [
            {"key": "silicone-mat", "asin": "B00ABCDE12", "display": "Silicone Baking Mat"}
        ]
        # no keyword hit -> _default -> storage -> treat-jar.
        berry = repo.get_recipe("berry-pupsicles")
        assert berry is not None
        assert berry.affiliate_products == [
            {"key": "treat-jar", "asin": "B00ZZZZ123", "display": "Treat Jar"}
        ]
    finally:
        conn.close()


def test_dry_run_does_not_persist(tmp_path: Path) -> None:
    conn, repo = _seed(tmp_path)
    try:
        report = AffiliateMatcher(repo, _catalog(tmp_path), limit=3).run(persist=False)
        assert report.persisted == 0
        row = repo.get_recipe("autumn-biscuits")
        assert row is not None
        assert row.affiliate_products == []
    finally:
        conn.close()
