"""Dog-safety scanner tests for recipe_db.safety. Pure, no network."""
# ruff: noqa: S101

from __future__ import annotations

import pytest

from recipe_db.models import Ingredient
from recipe_db.safety import (
    safety_note,
    scan_ingredient_lines,
    scan_ingredients,
)


def test_garlic_and_chocolate_flagged_not_safe() -> None:
    lines = [
        "2 cloves garlic, minced",
        "1 cup dark chocolate chips",
        "1 cup flour",
    ]
    flags, safe = scan_ingredient_lines(lines)
    assert safe is False
    assert "garlic" in flags
    assert "chocolate" in flags


def test_clean_dog_safe_lines_have_no_flags() -> None:
    lines = [
        "1 cup canned pumpkin puree",
        "1 ripe banana, mashed",
        "2 cups rolled oats",
    ]
    flags, safe = scan_ingredient_lines(lines)
    assert flags == []
    assert safe is True


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("1 tsp xylitol", "xylitol"),
        ("1/2 cup grapes", "grape"),
        ("1/4 cup raisins", "raisin"),
        ("1 small onion, diced", "onion"),
    ],
)
def test_individual_toxic_terms_flagged(line: str, expected: str) -> None:
    flags, safe = scan_ingredient_lines([line])
    assert safe is False
    assert expected in flags


def test_scan_ingredients_object_path_matches_line_path() -> None:
    ingredients = [
        Ingredient(item="garlic", qty="2", unit="cloves"),
        Ingredient(item="flour", qty="1", unit="cup", notes="contains raisins"),
    ]
    flags, safe = scan_ingredients(ingredients)
    assert safe is False
    assert "garlic" in flags
    # notes are scanned too.
    assert "raisin" in flags


def test_safety_note_warns_when_flags_present() -> None:
    note = safety_note(["garlic", "chocolate"])
    assert "WARNING" in note
    assert "garlic" in note
    assert "chocolate" in note


def test_safety_note_positive_when_no_flags() -> None:
    note = safety_note([])
    assert "WARNING" not in note
    assert "Dog-safe" in note
