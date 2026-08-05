# pyright: reportMissingImports=false
"""Service-level tests for the seasonal-selection phase (data-layer E2E)."""
# ruff: noqa: S101

from __future__ import annotations

import sqlite3
from pathlib import Path

from pipeline.seasonal_selection import SeasonalSelector
from recipe_db.db import connect, migrate
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository


def _seed(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    repo = RecipeRepository(conn)
    repo.upsert_recipe(
        RecipeRow(name="Pumpkin Spice Treats", tags=["pumpkin"], content_hash="h1")
    )
    repo.upsert_recipe(
        RecipeRow(
            name="Frozen Watermelon Pupsicles", tags=["frozen"], content_hash="h2"
        )
    )
    repo.upsert_recipe(
        RecipeRow(
            name="Everyday Chicken Biscuits", tags=["chicken"], content_hash="h3"
        )
    )
    return conn, repo


def _tags(repo: RecipeRepository, recipe_id: str) -> list[str]:
    row = repo.get_recipe(recipe_id)
    assert row is not None
    return row.season_tags


def test_selects_in_season_and_all_season(tmp_path: Path) -> None:
    conn, repo = _seed(tmp_path)
    try:
        report = SeasonalSelector(repo).run(season="fall", persist=True)
        assert report.total == 3
        # pumpkin (fall) + chicken (all-season); watermelon (summer) excluded.
        assert set(report.selected_ids) == {
            "pumpkin-spice-treats",
            "everyday-chicken-biscuits",
        }
        # inferred season_tags persisted for the seasonal recipes.
        assert _tags(repo, "pumpkin-spice-treats") == ["fall"]
        assert _tags(repo, "frozen-watermelon-pupsicles") == ["summer"]
    finally:
        conn.close()


def test_dry_run_does_not_persist(tmp_path: Path) -> None:
    conn, repo = _seed(tmp_path)
    try:
        report = SeasonalSelector(repo).run(season="summer", persist=False)
        assert set(report.selected_ids) == {
            "frozen-watermelon-pupsicles",
            "everyday-chicken-biscuits",
        }
        assert report.persisted == 0
        # nothing written to the DB in dry-run mode.
        assert _tags(repo, "frozen-watermelon-pupsicles") == []
    finally:
        conn.close()
