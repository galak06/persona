"""Pick up to N products for a recipe based on slug + title keywords.

Priority:
    1. recipe_overrides[slug]   — exact list, capped at limit
    2. recipe_type_map keyword match against slug+title — pull 1 product per
       mapped category, in the catalog's order, capped at limit
    3. _default categories      — same as #2 but using the fallback list
"""

from __future__ import annotations

from .catalog import RecipeCatalog, RecipeProduct

DEFAULT_LIMIT = 3


def _slug_token_set(slug: str, title: str) -> str:
    return f"{slug} {title}".lower()


def _categories_for_recipe(slug: str, title: str, catalog: RecipeCatalog) -> list[str]:
    haystack = _slug_token_set(slug, title)
    for keyword, cats in catalog.type_map.items():
        if keyword == "_default":
            continue
        if keyword in haystack:
            return cats
    return catalog.type_map["_default"]


def _from_overrides(slug: str, catalog: RecipeCatalog, limit: int) -> list[RecipeProduct] | None:
    keys = catalog.overrides.get(slug.lower())
    if not keys:
        return None
    picks: list[RecipeProduct] = []
    for key in keys[:limit]:
        product = catalog.get(key)
        if product is not None:
            picks.append(product)
    return picks


def _from_categories(
    categories: list[str], catalog: RecipeCatalog, limit: int
) -> list[RecipeProduct]:
    picks: list[RecipeProduct] = []
    seen_keys: set[str] = set()
    for cat in categories:
        for product in catalog.by_category.get(cat, []):
            if product.key in seen_keys:
                continue
            picks.append(product)
            seen_keys.add(product.key)
            break  # one per category — width over depth
        if len(picks) >= limit:
            break
    return picks[:limit]


def pick_products(
    slug: str,
    title: str,
    catalog: RecipeCatalog,
    limit: int = DEFAULT_LIMIT,
) -> list[RecipeProduct]:
    """Choose ≤limit products tailored to the recipe."""
    if limit <= 0:
        return []
    override = _from_overrides(slug, catalog, limit)
    if override is not None:
        return override
    categories = _categories_for_recipe(slug, title, catalog)
    return _from_categories(categories, catalog, limit)
