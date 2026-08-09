"""Merged candidate pool of affiliate products for the crew pipeline.

Two real catalogs feed content drafting today: the brand's flat blog
catalog (`data/config/affiliate_products.json`, loaded by
`lib.crew.writer.context.load_brand_affiliate_catalog`) and the recipe
catalog (`data/config/recipe_products.json`, loaded by
`lib.recipe_products.catalog.load_catalog`). This module merges both into
one `dict[str, ProductEntry]` so downstream selection sees every product
the brand can actually link, in the flat catalog's entry shape.

Tolerant-by-design like the writer's own loaders: a missing or invalid
recipe catalog degrades to flat-only with a warning, never an exception.
"""

from __future__ import annotations

from pathlib import Path

from lib.affiliate_resolver import ProductEntry
from lib.crew.writer.context import load_brand_affiliate_catalog
from lib.observability import get_logger
from lib.recipe_products.catalog import RecipeCatalogError, load_catalog

logger = get_logger(__name__)

_RECIPE_CATALOG_RELATIVE = Path("data") / "config" / "recipe_products.json"


def load_candidate_pool(brand_dir: Path) -> dict[str, ProductEntry]:
    """Merge the brand's flat blog catalog with its recipe catalog.

    Flat entries win on key collision (the flat catalog carries the
    curated, blog-facing display/notes); the losing recipe key is logged.
    Recipe `blurb` maps onto `ProductEntry.notes` so the pool has one
    uniform "why this product" field. Never raises: each source that
    can't be read simply contributes nothing.
    """
    pool = load_brand_affiliate_catalog(brand_dir)

    recipe_path = brand_dir / _RECIPE_CATALOG_RELATIVE
    try:
        recipe_catalog = load_catalog(recipe_path)
    except (RecipeCatalogError, FileNotFoundError, OSError) as exc:
        logger.warning(
            "crew_products_recipe_catalog_unavailable",
            path=str(recipe_path),
            error=str(exc),
        )
        return pool

    for key, product in recipe_catalog.products.items():
        if key in pool:
            logger.warning("crew_products_pool_collision_kept_flat_entry", key=key)
            continue
        pool[key] = ProductEntry(
            key=product.key,
            asin=product.asin,
            display=product.display,
            category=product.category,
            notes=product.blurb,
        )
    return pool
