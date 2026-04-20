"""WordPress publisher tests. Uses respx to mock all HTTP calls."""

from __future__ import annotations

import os

import httpx
import pytest
import respx

from generators.image import GeneratedImage
from generators.recipe import Recipe
from publishers import wordpress


@pytest.fixture(autouse=True)
def wp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # WP_URL is the preferred project convention and takes precedence over
    # WP_BASE_URL in publishers.wordpress._client(). Set both for safety.
    monkeypatch.setenv("WP_URL", "https://example.test")
    monkeypatch.setenv("WP_BASE_URL", "https://example.test")
    monkeypatch.setenv("WP_USER", "nallasdad")
    monkeypatch.setenv("WP_APP_PASSWORD_USER", "nallasdad")
    monkeypatch.setenv("WP_APP_PASSWORD", "abcd efgh ijkl mnop qrst uvwx")


@pytest.fixture
def recipe() -> Recipe:
    return Recipe(
        title="Beef Liver Training Treats",
        slug="beef-liver-training-treats",
        meta_description=(
            "Three-ingredient beef liver training treats you can bake in 25 minutes — "
            "the only currency Nalla actually works for. Pantry-easy, freezer-friendly."
        ),
        body_markdown=(
            "I make these for every new trick Nalla learns.\n\n"
            "## Ingredients\n- [ ] 1 lb beef liver\n\n"
            "## Instructions\n1. Preheat the oven.\n\n"
            "## Nalla's verdict\nShe stole the tray.\n\n"
            "## FAQ\n**Q:** Storage? **A:** Freezer 3 months.\n"
        ),
        ingredients=["1 lb beef liver", "1 egg", "1/2 cup oat flour"],
        steps=["Preheat the oven.", "Blend.", "Bake.", "Cool."],
        prep_minutes=10,
        cook_minutes=15,
        yield_servings="makes ~60 pea-sized treats",
        tags=["training-treats", "beef", "low-ingredient"],
        image_brief="Overhead shot of golden-brown pea-sized treats on parchment, warm light.",
        ig_caption=(
            "Liver treats are the only training currency Nalla takes seriously — bake your own in 25 minutes.\n\n"
            "Full recipe on the site (link in bio).\n\n"
            "What's your dog's highest-value treat? Drop it below 👇\n\n"
            "#doglife #dogrecipes #trainingtreats #nallasdad #dogfoodandfun #homemadedogtreats #dogsofinsta #dogfood"
        ),
    )


@pytest.fixture
def image() -> GeneratedImage:
    return GeneratedImage(
        url="https://cdn.example.test/generated/liver.png",
        alt_text="Beef Liver Training Treats — overhead shot on parchment",
        provider="replicate",
        bytes_=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
    )


@respx.mock
def test_publish_happy_path(recipe: Recipe, image: GeneratedImage) -> None:
    respx.post("https://example.test/wp-json/wp/v2/media").respond(
        201, json={"id": 501, "source_url": "https://example.test/wp-content/uploads/liver.png"}
    )
    respx.get("https://example.test/wp-json/wp/v2/categories").respond(
        200, json=[{"id": 42, "slug": "recipes", "name": "Recipes"}]
    )
    respx.get("https://example.test/wp-json/wp/v2/tags").respond(200, json=[])
    respx.post("https://example.test/wp-json/wp/v2/tags").respond(
        201, json={"id": 999, "slug": "training-treats"}
    )
    respx.post("https://example.test/wp-json/wp/v2/posts").respond(
        201,
        json={
            "id": 2233,
            "link": "https://example.test/beef-liver-training-treats/",
        },
    )
    respx.post("https://example.test/wp-json/surerank/v1/post/settings").respond(
        200, json={"success": True}
    )
    respx.post("https://example.test/wp-json/wp/v2/media/501").respond(
        200, json={"id": 501, "alt_text": image.alt_text}
    )

    result = wordpress.publish_to_wordpress(recipe, image)

    assert result.post_id == 2233
    assert result.permalink.endswith("/beef-liver-training-treats/")
    assert result.featured_image_url.endswith("/liver.png")
    assert result.warnings == []


@respx.mock
def test_surerank_failure_is_warning_not_error(recipe: Recipe, image: GeneratedImage) -> None:
    respx.post("https://example.test/wp-json/wp/v2/media").respond(
        201, json={"id": 1, "source_url": "https://example.test/x.png"}
    )
    respx.get("https://example.test/wp-json/wp/v2/categories").respond(200, json=[{"id": 42}])
    respx.get("https://example.test/wp-json/wp/v2/tags").respond(200, json=[])
    respx.post("https://example.test/wp-json/wp/v2/tags").respond(201, json={"id": 1})
    respx.post("https://example.test/wp-json/wp/v2/posts").respond(
        201, json={"id": 10, "link": "https://example.test/x/"}
    )
    respx.post("https://example.test/wp-json/surerank/v1/post/settings").respond(500, text="boom")
    respx.post("https://example.test/wp-json/wp/v2/media/1").respond(200, json={})

    result = wordpress.publish_to_wordpress(recipe, image)

    assert result.post_id == 10
    assert any("SureRank" in w for w in result.warnings)


@respx.mock
def test_post_create_failure_raises(recipe: Recipe, image: GeneratedImage) -> None:
    respx.post("https://example.test/wp-json/wp/v2/media").respond(
        201, json={"id": 1, "source_url": "https://example.test/x.png"}
    )
    respx.get("https://example.test/wp-json/wp/v2/categories").respond(200, json=[{"id": 42}])
    respx.get("https://example.test/wp-json/wp/v2/tags").respond(200, json=[])
    respx.post("https://example.test/wp-json/wp/v2/tags").respond(201, json={"id": 1})
    respx.post("https://example.test/wp-json/wp/v2/posts").respond(400, text="bad payload")

    with pytest.raises(wordpress.WordPressError):
        wordpress.publish_to_wordpress(recipe, image)


def test_recipe_jsonld_shape(recipe: Recipe) -> None:
    schema = wordpress._recipe_jsonld(recipe)
    assert schema["@type"] == "Recipe"
    assert schema["prepTime"] == "PT10M"
    assert schema["totalTime"] == "PT25M"
    assert len(schema["recipeInstructions"]) == len(recipe.steps)
    assert schema["recipeInstructions"][0]["position"] == 1
