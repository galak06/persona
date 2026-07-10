# pyright: reportMissingImports=false
"""affiliate_products round-trip + setter tests for recipe_db. No network."""
# ruff: noqa: S101

from __future__ import annotations

import sqlite3
from pathlib import Path

from recipe_db.db import connect, migrate
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository


def _repo(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn, RecipeRepository(conn)


def test_affiliate_products_default_empty(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.upsert_recipe(RecipeRow(name="Plain Biscuits", content_hash="a"))
        row = repo.get_recipe("plain-biscuits")
        assert row is not None
        assert row.affiliate_products == []
    finally:
        conn.close()


def test_affiliate_products_round_trip(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    products = [{"key": "treat-jar", "asin": "B00ZZZZ123", "display": "Treat Jar"}]
    try:
        repo.upsert_recipe(
            RecipeRow(name="Jar Treats", content_hash="b", affiliate_products=products)
        )
        row = repo.get_recipe("jar-treats")
        assert row is not None
        assert row.affiliate_products == products
    finally:
        conn.close()


def test_set_affiliate_products_updates(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    products = [{"key": "mat", "asin": "B00ABCDE12", "display": "Baking Mat"}]
    try:
        repo.upsert_recipe(RecipeRow(name="Mat Cookies", content_hash="c"))
        repo.set_affiliate_products("mat-cookies", products)
        row = repo.get_recipe("mat-cookies")
        assert row is not None
        assert row.affiliate_products == products
    finally:
        conn.close()
