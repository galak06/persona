# pyright: reportMissingImports=false
"""season_tags round-trip + setter tests for recipe_db. No network."""
# ruff: noqa: S101

from __future__ import annotations

import sqlite3
from pathlib import Path

from recipe_db.db import connect, migrate
from recipe_db.models import RecipeRow, RecipeStatus
from recipe_db.repository import RecipeRepository


def _repo(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn, RecipeRepository(conn)


def test_season_tags_default_empty(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.upsert_recipe(
            RecipeRow(name="Plain Biscuits", status=RecipeStatus.NORMALIZED)
        )
        row = repo.get_recipe("plain-biscuits")
        assert row is not None
        assert row.season_tags == []
    finally:
        conn.close()


def test_season_tags_round_trip(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.upsert_recipe(RecipeRow(name="Pumpkin Treats", season_tags=["fall"]))
        row = repo.get_recipe("pumpkin-treats")
        assert row is not None
        assert row.season_tags == ["fall"]
    finally:
        conn.close()


def test_set_season_tags_updates(tmp_path: Path) -> None:
    conn, repo = _repo(tmp_path)
    try:
        repo.upsert_recipe(RecipeRow(name="Berry Pupsicles"))
        repo.set_season_tags("berry-pupsicles", ["summer"])
        row = repo.get_recipe("berry-pupsicles")
        assert row is not None
        assert row.season_tags == ["summer"]
    finally:
        conn.close()
