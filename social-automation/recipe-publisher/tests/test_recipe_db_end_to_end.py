"""End-to-end: scrape -> normalize -> safety -> seed export -> load_seeds.

Proves the recipe_db exporter output satisfies the real seed consumer
(generators.seeds.load_seeds). No network — uses the bundled fixture HTML.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path

import pytest

from generators.seeds import RecipeSeed, load_seeds
from recipe_db.models import RecipeRow
from recipe_db.normalize import normalize
from recipe_db.safety import scan_ingredients
from recipe_db.scraper import scrape
from recipe_db.seed_exporter import export_seed, recipe_to_seed

_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "recipe_db"
    / "tests_fixtures"
    / "sample_recipe.html"
)


def _scraped_row() -> RecipeRow:
    """Run the fixture through scrape -> normalize -> safety into a RecipeRow."""
    html = _FIXTURE.read_text(encoding="utf-8")
    raw = scrape("https://example.com/pumpkin-oat-treats", html=html)
    assert raw is not None
    scraped = normalize(raw, "https://example.com/pumpkin-oat-treats")
    flags, safe = scan_ingredients(scraped.ingredients)
    return RecipeRow(
        name=scraped.name,
        ingredients=scraped.ingredients,
        steps=scraped.steps,
        prep_minutes=scraped.prep_minutes,
        cook_minutes=scraped.cook_minutes,
        total_minutes=scraped.total_minutes,
        servings=scraped.servings,
        nutrition=scraped.nutrition,
        category=scraped.category,
        tags=scraped.tags,
        hero_image_url=scraped.hero_image_url,
        source_url=scraped.source_url,
        source_name=scraped.source_name,
        license=scraped.license,
        content_hash=scraped.content_hash,
        toxic_flags=flags,
        dog_safe=safe,
    )


def test_scrape_normalize_extracts_expected_fields() -> None:
    html = _FIXTURE.read_text(encoding="utf-8")
    raw = scrape("https://example.com/pumpkin-oat-treats", html=html)
    assert raw is not None
    scraped = normalize(raw, "https://example.com/pumpkin-oat-treats")

    assert scraped.name == "Pumpkin Oat Dog Treats"
    assert scraped.prep_minutes == 10
    assert scraped.cook_minutes == 25
    assert scraped.total_minutes == 35
    assert len(scraped.ingredients) == 4
    assert len(scraped.steps) == 4
    assert scraped.source_name == "example.com"


def test_fixture_is_dog_safe() -> None:
    row = _scraped_row()
    assert row.dog_safe is True
    assert row.toxic_flags == []


def test_seed_dict_satisfies_load_seeds_consumer(tmp_path: Path) -> None:
    row = _scraped_row()
    seed_dict = recipe_to_seed(row)

    seeds_path = tmp_path / "seeds.json"
    wrapper = {
        "_schema_version": "1",
        "_note": "test",
        "seeds": [seed_dict],
    }
    seeds_path.write_text(json.dumps(wrapper), encoding="utf-8")

    seeds = load_seeds(seeds_path)
    assert len(seeds) == 1
    seed = seeds[0]
    assert isinstance(seed, RecipeSeed)

    # Every required field is populated (no empty required field).
    assert seed.id
    assert seed.title == "Pumpkin Oat Dog Treats"
    assert seed.topic_keywords  # non-empty list
    assert seed.category
    assert seed.yield_servings
    assert seed.tags
    assert seed.ingredients
    assert seed.steps
    assert seed.dog_safety_notes
    assert seed.storage
    assert seed.source_attribution

    # Ingredients are flat strings, not dataclasses/dicts.
    assert all(isinstance(line, str) for line in seed.ingredients)
    assert all(line.strip() for line in seed.ingredients)

    # portion_guide has the three size keys.
    assert set(seed.portion_guide.keys()) == {"small", "medium", "large"}
    assert all(seed.portion_guide.values())

    # Durations are real ints.
    assert isinstance(seed.prep_minutes, int)
    assert isinstance(seed.cook_minutes, int)
    assert seed.prep_minutes == 10
    assert seed.cook_minutes == 25


def test_export_seed_refuses_unsafe_row_without_override(tmp_path: Path) -> None:
    row = _scraped_row()
    row.dog_safe = False
    row.toxic_flags = ["garlic"]
    row.override = False

    with pytest.raises(ValueError, match="refusing to export unsafe"):
        export_seed(row, seeds_path=tmp_path / "seeds.json")


def test_export_seed_allows_unsafe_row_with_override(tmp_path: Path) -> None:
    row = _scraped_row()
    row.dog_safe = False
    row.toxic_flags = ["garlic"]
    row.override = True

    seeds_path = tmp_path / "seeds.json"
    seed = export_seed(row, seeds_path=seeds_path)
    assert seed["id"]
    # File was written and is loadable by the real consumer.
    seeds = load_seeds(seeds_path)
    assert len(seeds) == 1
