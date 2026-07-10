"""Tests for category crawling: link extraction, rating filter, batch flow."""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path

import pytest
from recipe_db import batch, normalize, scraper


def _recipe_html(name: str, rating_count: int) -> str:
    """Minimal page embedding a JSON-LD Recipe with an aggregateRating."""
    payload = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": name,
        "recipeIngredient": ["1 cup rolled oats", "1 ripe banana, mashed"],
        "recipeInstructions": [
            {"@type": "HowToStep", "text": "Mix."},
            {"@type": "HowToStep", "text": "Bake."},
        ],
        "prepTime": "PT10M",
        "cookTime": "PT20M",
        "recipeYield": "12 treats",
        "aggregateRating": {
            "@type": "AggregateRating",
            "ratingValue": "4.7",
            "ratingCount": str(rating_count),
        },
    }
    block = json.dumps(payload)
    return f"<html><head><script type='application/ld+json'>{block}"\
           f"</script></head><body>{name}</body></html>"


_POPULAR = "https://www.allrecipes.com/recipe/111/popular-dog-treats/"
_NICHE = "https://www.allrecipes.com/recipe/222/niche-dog-treats/"

_LISTING_HTML = (
    "<html><body>"
    f"<a href='{_POPULAR}'>Popular</a>"
    f"<a href='{_NICHE}'>Niche</a>"
    f"<a href='{_POPULAR}'>Popular dup</a>"  # duplicate must collapse
    "<a href='https://www.allrecipes.com/about/'>not a recipe</a>"
    "</body></html>"
)


def test_extract_recipe_links_dedups_and_filters() -> None:
    links = scraper.extract_recipe_links(_LISTING_HTML)
    assert links == [_POPULAR.rstrip("/"), _NICHE.rstrip("/")]


def test_normalize_parses_core_fields() -> None:
    raw = scraper.scrape(_POPULAR, html=_recipe_html("Popular", 1388))
    assert raw is not None
    recipe = normalize.normalize(raw, _POPULAR)
    assert recipe.name == "Popular"
    assert recipe.prep_minutes == 10
    assert len(recipe.ingredients) == 2


def _fake_fetcher() -> batch.Fetcher:
    # extract_recipe_links strips trailing slashes, so key recipe pages by the
    # stripped URL the crawler will actually request.
    pages = {
        "https://www.allrecipes.com/recipes/1951/pet-food/": _LISTING_HTML,
        _POPULAR.rstrip("/"): _recipe_html("Popular Dog Treats", 1388),
        _NICHE.rstrip("/"): _recipe_html("Niche Dog Treats", 10),
    }

    def fetch(url: str) -> str:
        return pages[url]

    return fetch


def test_scrape_category_dry_run_stores_all_links() -> None:
    summary = batch.scrape_category(
        "https://www.allrecipes.com/recipes/1951/pet-food/",
        dry_run=True,
        delay_seconds=0,
        fetch=_fake_fetcher(),
    )
    assert summary.found_links == 2
    assert summary.count(batch.WOULD_STORE) == 2
    names = sorted(o.name for o in summary.outcomes)
    assert names == ["Niche Dog Treats", "Popular Dog Treats"]


def test_scrape_category_applies_namer(tmp_path: Path) -> None:
    from recipe_db import db
    from recipe_db.repository import RecipeRepository

    conn = db.connect(tmp_path / "r.db")
    db.migrate(conn)
    repo = RecipeRepository(conn)

    def namer(name: str, ingredients: list[str]) -> str:
        return f"Brand {name}"

    summary = batch.scrape_category(
        "https://www.allrecipes.com/recipes/1951/pet-food/",
        dry_run=False,
        delay_seconds=0,
        fetch=_fake_fetcher(),
        repo=repo,
        namer=namer,
    )
    assert summary.count(batch.STORED) == 2
    rows = repo.list_recipes()
    assert rows and all(r.display_name.startswith("Brand ") for r in rows)
    conn.close()


def test_generate_display_name_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from recipe_db import rename

    assert rename.generate_display_name("Some Recipe", ["flour"]) == ""
