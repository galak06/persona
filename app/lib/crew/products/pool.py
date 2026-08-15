"""Merged candidate pool of affiliate products for the crew pipeline.

Three sources feed content drafting today: the brand's flat blog catalog
(`data/config/affiliate_products.json`, loaded by
`lib.crew.writer.context.load_brand_affiliate_catalog`), the recipe
catalog (`data/config/recipe_products.json`, loaded by
`lib.recipe_products.catalog.load_catalog`), and the discovery cache
(`data/cache/discovered_products.json`, written by
`lib.crew.products.discovery`). This module merges all three into one
`dict[str, ProductEntry]` so downstream selection AND
`[AFFILIATE:key]` resolution see every product the brand can actually
link, in the flat catalog's entry shape.

Tolerant-by-design like the writer's own loaders: a missing or invalid
recipe catalog or discovery cache degrades to whatever else loaded, with
a warning, never an exception.
"""

from __future__ import annotations

from pathlib import Path

from lib.affiliate_resolver import ProductEntry
from lib.crew.products.discovery import load_discovered_products
from lib.crew.writer.context import load_brand_affiliate_catalog
from lib.observability import get_logger
from lib.recipe_products.catalog import RecipeCatalogError, load_catalog

logger = get_logger(__name__)

_RECIPE_CATALOG_RELATIVE = Path("data") / "config" / "recipe_products.json"


def load_candidate_pool(brand_dir: Path) -> dict[str, ProductEntry]:
    """Merge the brand's flat blog catalog, recipe catalog, and discoveries.

    Precedence is curated-first: a flat entry wins any key collision (it
    carries the editorial display/notes the selector reasons over), then
    recipe entries, then live-discovered ones. Recipe `blurb` maps onto
    `ProductEntry.notes` so the pool has one uniform "why this product"
    field. Never raises: each source that can't be read simply
    contributes nothing.
    """
    pool = load_brand_affiliate_catalog(brand_dir)
    _merge_recipe_catalog(brand_dir, pool)
    _merge_discovered(brand_dir, pool)
    return pool


def _merge_recipe_catalog(brand_dir: Path, pool: dict[str, ProductEntry]) -> None:
    """Add recipe-catalog products the flat catalog doesn't already define."""
    recipe_path = brand_dir / _RECIPE_CATALOG_RELATIVE
    try:
        recipe_catalog = load_catalog(recipe_path)
    except (RecipeCatalogError, FileNotFoundError, OSError) as exc:
        logger.warning(
            "crew_products_recipe_catalog_unavailable",
            path=str(recipe_path),
            error=str(exc),
        )
        return

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


def _merge_discovered(brand_dir: Path, pool: dict[str, ProductEntry]) -> None:
    """Add cached discoveries BEHIND both curated catalogs.

    Without this the drafting path contradicts itself: it runs the selector
    with `discover=True`, so the writer embeds `[AFFILIATE:<discovered-key>]`
    placeholders for products found live, and then `assemble_final_html`
    resolves against this pool -- which, missing those entries, raised
    `AffiliateResolverError` on every post whose topic the curated catalogs
    didn't already cover. Live-reproduced 2026-08-14: five approved ideas
    each bounced back to status='approved' by the API's revert path, a full
    2,200-word draft discarded on every retry.
    """
    added: list[str] = []
    for key, entry in load_discovered_products(brand_dir).items():
        if key in pool:
            continue
        pool[key] = entry
        added.append(key)
    if added:
        logger.info("crew_products_pool_discovered_merged", count=len(added), keys=added)
