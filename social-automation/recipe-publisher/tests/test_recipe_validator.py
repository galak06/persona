"""Unit tests for the Recipe validator. No network."""

from __future__ import annotations

import pytest

from generators import recipe as recipe_mod


def _make(**overrides) -> recipe_mod.Recipe:
    base = dict(
        title="Beef Liver Training Treats",
        slug="beef-liver-training-treats",
        meta_description=(
            "Three-ingredient beef liver training treats you can bake in 25 minutes — "
            "the only currency Nalla actually works for. Pantry-easy, freezer-friendly."
        ),
        body_markdown="intro\n## Ingredients\n- x\n## Instructions\n1. x\n",
        ingredients=["2 lb beef liver", "1 cup oat flour", "1 large egg"],
        steps=["trim liver", "slice thin", "bake at 300F for 2 hours"],
        prep_minutes=10,
        cook_minutes=15,
        yield_servings="~60",
        tags=["treats"],
        image_brief="overhead",
        ig_caption=(
            "A hook that fits in 125 chars and earns the scroll-stop.\n\n"
            "\u2022 25 min, 3 ingredients\n"
            "\u2022 Oven at 300\u00b0F for two hours\n"
            "\u2022 Freezer-friendly up to 6 months\n\n"
            "Comment RECIPE and I'll DM you the link.\n\n"
            "What treat does your dog work hardest for?\n\n"
            "#doglife #dogrecipes #nallasdad #dogfoodandfun"
        ),
    )
    base.update(overrides)
    return recipe_mod.Recipe(**base)


def test_valid_recipe_passes() -> None:
    recipe_mod._validate(_make())


def test_meta_description_too_short_rejected() -> None:
    with pytest.raises(ValueError, match="meta_description length"):
        recipe_mod._validate(_make(meta_description="too short"))


def test_medical_claim_rejected() -> None:
    with pytest.raises(ValueError, match="medical-claim language"):
        recipe_mod._validate(
            _make(body_markdown="This recipe cures all dog ailments.")
        )


def test_empty_ingredients_rejected() -> None:
    with pytest.raises(ValueError, match="at least one ingredient"):
        recipe_mod._validate(_make(ingredients=[]))


def test_caption_missing_bullets_rejected() -> None:
    bad = (
        "A hook that fits in 125 chars and earns the scroll-stop.\n\n"
        "No bullets here at all.\n\n"
        "Comment RECIPE and I'll DM you the link.\n\n"
        "What treat does your dog work hardest for?\n\n"
        "#nallasdad #dogfoodandfun"
    )
    with pytest.raises(ValueError, match="bullet-fact"):
        recipe_mod._validate(_make(ig_caption=bad))


def test_caption_missing_comment_cta_rejected() -> None:
    bad = (
        "A hook that fits in 125 chars and earns the scroll-stop.\n\n"
        "\u2022 25 min, 3 ingredients\n"
        "\u2022 Oven at 300 degrees\n"
        "\u2022 Freezer-friendly\n\n"
        "Link in bio for the recipe.\n\n"
        "What treat does your dog love most?\n\n"
        "#nallasdad #dogfoodandfun"
    )
    with pytest.raises(ValueError, match="comment-gated CTA"):
        recipe_mod._validate(_make(ig_caption=bad))


def test_caption_missing_branded_hashtag_rejected() -> None:
    bad = (
        "A hook that fits in 125 chars and earns the scroll-stop.\n\n"
        "\u2022 25 min, 3 ingredients\n"
        "\u2022 Oven at 300 degrees\n"
        "\u2022 Freezer-friendly\n\n"
        "Comment RECIPE and I'll DM you the link.\n\n"
        "What treat does your dog love most?\n\n"
        "#doglife #dogrecipes"
    )
    with pytest.raises(ValueError, match="branded hashtag"):
        recipe_mod._validate(_make(ig_caption=bad))


def test_slugify() -> None:
    assert recipe_mod._slugify("Beef Liver Training Treats!") == "beef-liver-training-treats"
    assert recipe_mod._slugify("Nalla's Verdict: 10/10") == "nallas-verdict-1010"
