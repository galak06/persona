"""Repository round-trip + dedup + status tests for recipe_db. No network."""
# ruff: noqa: S101

from __future__ import annotations

import sqlite3
from pathlib import Path

from recipe_db.db import connect, migrate
from recipe_db.models import Ingredient, RecipeRow, RecipeStatus
from recipe_db.repository import RecipeRepository


def _repo(tmp_path: Path) -> tuple[sqlite3.Connection, RecipeRepository]:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn, RecipeRepository(conn)


def _sample_row(**overrides: object) -> RecipeRow:
    base: dict[str, object] = dict(
        name="Pumpkin Oat Dog Treats",
        ingredients=[
            Ingredient(item="canned pumpkin puree", qty="1", unit="cup"),
            Ingredient(item="rolled oats", qty="2", unit="cups"),
        ],
        steps=["Mix everything.", "Bake 25 minutes."],
        prep_minutes=10,
        cook_minutes=25,
        total_minutes=35,
        servings="24 treats",
        nutrition={"calories": "32 kcal"},
        category="Dog Treats",
        tags=["pumpkin", "oats"],
        hero_image_url="https://example.com/img.jpg",
        source_url="https://example.com/recipe",
        source_name="example.com",
        content_hash="hash-1",
        status=RecipeStatus.NORMALIZED,
        toxic_flags=[],
        dog_safe=True,
    )
    base.update(overrides)
    return RecipeRow(**base)  # type: ignore[arg-type]


def test_migrate_creates_expected_tables(tmp_path: Path) -> None:
    conn, _ = _repo(tmp_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert "raw_scrapes" in names
    assert "recipes" in names
    assert "recipes_fts" in names


def test_insert_raw_dedups_on_content_hash(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    first = repo.insert_raw(
        source_url="https://example.com/r",
        source_name="example.com",
        payload={"name": "x"},
        content_hash="dup-hash",
        scraped_at="2026-06-08T00:00:00Z",
    )
    second = repo.insert_raw(
        source_url="https://example.com/r",
        source_name="example.com",
        payload={"name": "x"},
        content_hash="dup-hash",
        scraped_at="2026-06-08T00:01:00Z",
    )
    assert first is True
    assert second is False


def test_upsert_then_get_round_trips_json_fields(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    row = _sample_row()
    repo.upsert_recipe(row)

    fetched = repo.get_recipe(row.ensure_id())
    assert fetched is not None
    # JSON list/dict fields deserialize back to the right Python types.
    assert isinstance(fetched.steps, list)
    assert fetched.steps == ["Mix everything.", "Bake 25 minutes."]
    assert isinstance(fetched.nutrition, dict)
    assert fetched.nutrition == {"calories": "32 kcal"}
    assert isinstance(fetched.tags, list)
    assert fetched.tags == ["pumpkin", "oats"]
    # Ingredients come back as Ingredient instances.
    assert all(isinstance(i, Ingredient) for i in fetched.ingredients)
    assert fetched.ingredients[0].item == "canned pumpkin puree"
    assert fetched.ingredients[0].qty == "1"
    assert fetched.ingredients[0].unit == "cup"
    # Scalars + booleans round-trip.
    assert fetched.prep_minutes == 10
    assert fetched.cook_minutes == 25
    assert fetched.dog_safe is True
    assert fetched.status == RecipeStatus.NORMALIZED


def test_get_recipe_missing_returns_none(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    assert repo.get_recipe("does-not-exist") is None


def test_upsert_is_idempotent_on_id(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    repo.upsert_recipe(_sample_row())
    repo.upsert_recipe(_sample_row(category="Updated Category"))
    rows = repo.list_recipes()
    assert len(rows) == 1
    assert rows[0].category == "Updated Category"


def test_set_status_updates_and_list_filters(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    row = _sample_row()
    repo.upsert_recipe(row)
    recipe_id = row.ensure_id()

    repo.set_status(recipe_id, RecipeStatus.SEED_EXPORTED)
    assert repo.get_recipe(recipe_id).status == RecipeStatus.SEED_EXPORTED  # type: ignore[union-attr]

    exported = repo.list_recipes(status=RecipeStatus.SEED_EXPORTED)
    assert [r.id for r in exported] == [recipe_id]
    assert repo.list_recipes(status=RecipeStatus.NORMALIZED) == []


def test_set_safety_updates_flags_and_dog_safe(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    row = _sample_row(dog_safe=True, toxic_flags=[])
    repo.upsert_recipe(row)
    recipe_id = row.ensure_id()

    repo.set_safety(recipe_id, ["garlic", "chocolate"], dog_safe=False)
    fetched = repo.get_recipe(recipe_id)
    assert fetched is not None
    assert fetched.toxic_flags == ["garlic", "chocolate"]
    assert fetched.dog_safe is False


def test_exists_helpers(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    row = _sample_row(content_hash="exists-hash")
    repo.upsert_recipe(row)
    assert repo.exists_by_id(row.ensure_id()) is True
    assert repo.exists_by_id("nope") is False
    assert repo.exists_by_content_hash("exists-hash") is True
    assert repo.exists_by_content_hash("absent-hash") is False
