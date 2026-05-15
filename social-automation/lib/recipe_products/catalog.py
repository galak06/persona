"""Load + validate data/recipe_products.json.

Single responsibility: deserialize JSON into typed dataclasses and surface
clear errors when the catalog is malformed. No matching logic, no rendering.
"""

from __future__ import annotations

import json
from lib.config import settings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

DEFAULT_CATALOG_PATH: Final[Path] = (
    settings.paths.data_dir / "recipe_products.json"
)


class RecipeCatalogError(ValueError):
    """Raised when the JSON catalog is missing required fields or has duplicate keys."""


@dataclass(frozen=True)
class RecipeProduct:
    key: str
    asin: str
    display: str
    blurb: str
    category: str


@dataclass(frozen=True)
class RecipeCatalog:
    products: dict[str, RecipeProduct] = field(default_factory=dict)
    by_category: dict[str, list[RecipeProduct]] = field(default_factory=dict)
    type_map: dict[str, list[str]] = field(default_factory=dict)
    overrides: dict[str, list[str]] = field(default_factory=dict)

    def get(self, key: str) -> RecipeProduct | None:
        return self.products.get(key)


def _validate_product(category: str, raw: dict, seen_keys: set[str]) -> RecipeProduct:
    for required in ("key", "asin", "display", "blurb"):
        if not raw.get(required):
            raise RecipeCatalogError(
                f"category '{category}' has a product missing required field '{required}'"
            )
    key = raw["key"]
    if key in seen_keys:
        raise RecipeCatalogError(f"duplicate product key across categories: '{key}'")
    seen_keys.add(key)
    asin = raw["asin"].strip()
    if len(asin) != 10:
        raise RecipeCatalogError(
            f"product '{key}' has invalid ASIN '{asin}' — expected 10 chars"
        )
    return RecipeProduct(
        key=key,
        asin=asin,
        display=raw["display"],
        blurb=raw["blurb"],
        category=category,
    )


def load_catalog(path: Path | None = None) -> RecipeCatalog:
    """Read and validate the JSON catalog. Raises RecipeCatalogError on any issue."""
    catalog_path = path or DEFAULT_CATALOG_PATH
    if not catalog_path.exists():
        raise RecipeCatalogError(f"catalog not found at {catalog_path}")

    try:
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RecipeCatalogError(f"catalog at {catalog_path} is not valid JSON: {exc}") from exc

    categories_raw = raw.get("categories", {})
    if not isinstance(categories_raw, dict):
        raise RecipeCatalogError("'categories' must be an object")

    products: dict[str, RecipeProduct] = {}
    by_category: dict[str, list[RecipeProduct]] = {}
    seen_keys: set[str] = set()
    for cat_name, items in categories_raw.items():
        if not isinstance(items, list):
            raise RecipeCatalogError(f"category '{cat_name}' must be an array")
        cat_products: list[RecipeProduct] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            product = _validate_product(cat_name, item, seen_keys)
            products[product.key] = product
            cat_products.append(product)
        by_category[cat_name] = cat_products

    type_map_raw = raw.get("recipe_type_map", {})
    if not isinstance(type_map_raw, dict):
        raise RecipeCatalogError("'recipe_type_map' must be an object")
    type_map: dict[str, list[str]] = {}
    for key, cats in type_map_raw.items():
        if not isinstance(cats, list) or not all(isinstance(c, str) for c in cats):
            raise RecipeCatalogError(f"recipe_type_map['{key}'] must be a list of category names")
        unknown = [c for c in cats if c not in by_category]
        if unknown:
            raise RecipeCatalogError(
                f"recipe_type_map['{key}'] references unknown categories: {unknown}"
            )
        type_map[key.lower()] = cats

    if "_default" not in type_map:
        raise RecipeCatalogError("recipe_type_map must include a '_default' fallback")

    overrides_raw = raw.get("recipe_overrides", {})
    if not isinstance(overrides_raw, dict):
        raise RecipeCatalogError("'recipe_overrides' must be an object")
    overrides: dict[str, list[str]] = {}
    for slug, keys in overrides_raw.items():
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise RecipeCatalogError(f"recipe_overrides['{slug}'] must be a list of product keys")
        unknown = [k for k in keys if k not in products]
        if unknown:
            raise RecipeCatalogError(
                f"recipe_overrides['{slug}'] references unknown product keys: {unknown}"
            )
        overrides[slug.lower()] = keys

    return RecipeCatalog(
        products=products,
        by_category=by_category,
        type_map=type_map,
        overrides=overrides,
    )
