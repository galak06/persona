"""Recipe-campaign product catalog: pick + render the "Our Pick: Tools Used in This Recipe" block.

Public surface:
    load_catalog()           — load and validate data/recipe_products.json
    pick_products(slug, ...) — choose ≤3 products for a given recipe
    render_block(...)        — render the HTML block (includes FTC disclosure)
    insert_or_replace_block — idempotent insert before the post's FAQ section
"""

from .block_renderer import (
    BLOCK_MARKER_CLOSE,
    BLOCK_MARKER_OPEN,
    insert_or_replace_block,
    render_block,
)
from .catalog import RecipeCatalog, RecipeProduct, load_catalog
from .matcher import pick_products

__all__ = [
    "BLOCK_MARKER_CLOSE",
    "BLOCK_MARKER_OPEN",
    "RecipeCatalog",
    "RecipeProduct",
    "insert_or_replace_block",
    "load_catalog",
    "pick_products",
    "render_block",
]
