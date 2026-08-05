"""Recipe seed schema validation.

Single responsibility: given a dict, return list of human-readable error
strings. Empty list = valid. No I/O, no LLM calls.

Schema mirrors recipe-publisher/seeds/seeds.json existing entries — adding a
new field here means updating both the validator AND any seed consumer.
"""

from __future__ import annotations

from typing import Any, Final

# Allowed recipe categories — keep tight so reels/IG carousel templates can
# fan out per category without surprises.
ALLOWED_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "treats-baked",
        "treats-frozen",
        "treats-no-bake",
        "treats-dehydrated",
        "meals-cooked",
        "meals-raw",
        "broths-soups",
        "stews",
    }
)

REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "title",
    "topic_keywords",
    "category",
    "prep_minutes",
    "cook_minutes",
    "yield_servings",
    "tags",
    "ingredients",
    "steps",
    "dog_safety_notes",
    "storage",
    "portion_guide",
    "source_attribution",
)

# Hard ban: any of these in an ingredient string is an automatic reject.
TOXIC_INGREDIENT_TOKENS: Final[tuple[str, ...]] = (
    "xylitol",
    "chocolate",
    "cocoa",
    "raisin",
    "grape",
    "macadamia",
    "onion powder",
    "garlic powder",
    "raw onion",
    "raw garlic",
    "cooked onion",
    "nutmeg",
    "alcohol",
    "caffeine",
    "coffee",
)


def validate_seed(seed: dict[str, Any]) -> list[str]:
    """Return a list of error messages. Empty list = valid."""
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in seed:
            errors.append(f"missing required field: {field}")

    if errors:
        return errors  # don't keep checking if structure is broken

    if not isinstance(seed["id"], str) or not seed["id"].strip():
        errors.append("id must be a non-empty string")
    if not isinstance(seed["title"], str) or len(seed["title"]) < 5:
        errors.append("title must be a string ≥5 chars")

    cat = seed["category"]
    if cat not in ALLOWED_CATEGORIES:
        errors.append(f"category '{cat}' not in allowed set: {sorted(ALLOWED_CATEGORIES)}")

    for int_field in ("prep_minutes", "cook_minutes"):
        v = seed[int_field]
        if not isinstance(v, int) or v < 0 or v > 600:
            errors.append(f"{int_field} must be int in [0, 600], got {v!r}")

    for list_field, min_len in (("topic_keywords", 3), ("tags", 2), ("ingredients", 3), ("steps", 3)):
        v = seed[list_field]
        if not isinstance(v, list) or len(v) < min_len:
            errors.append(f"{list_field} must be a list with ≥{min_len} items")
        elif not all(isinstance(x, str) and x.strip() for x in v):
            errors.append(f"{list_field} must contain only non-empty strings")

    pg = seed["portion_guide"]
    if not isinstance(pg, dict) or not all(k in pg for k in ("small", "medium", "large")):
        errors.append("portion_guide must include keys: small, medium, large")

    # Dog-safety scan on ingredients
    if isinstance(seed.get("ingredients"), list):
        joined = " ".join(str(i).lower() for i in seed["ingredients"])
        for token in TOXIC_INGREDIENT_TOKENS:
            if token in joined and "xylitol-free" not in joined and "no " + token not in joined:
                errors.append(f"ingredient list contains banned token '{token}' — reject")

    return errors
