"""Affiliate-product selection for the crew content pipeline.

Public surface:
    from lib.crew.products import select_products_for_post

`lib.crew.products.pool` merges the brand's flat blog catalog with the
recipe catalog into one candidate pool of
`lib.affiliate_resolver.ProductEntry` values, `lib.crew.products.usage` is
the append-only "recently used products" JSONL tracker, and
`lib.crew.products.selector` composes both with the selector agent
(`agent`/`prompts`/`execute`) into `select_products_for_post` -- the
per-post curated catalog `lib.crew.writer.orchestrator.write_post_from_brief`
feeds the writer prompt.

A second, backfill-facing path serves ALREADY-PUBLISHED posts
(`scripts/backfill_blog_product_blocks.py`):
`brief_from_post.synthesize_brief` rebuilds a brief from live post HTML,
`blog_selection.select_products_for_existing_post` runs the same agent but
skips (never falls back) on failure, `block` renders the blog picks block,
and `blog_backfill` holds the WordPress-side helpers.
"""

from lib.crew.products.block import (
    BLOG_BLOCK_CLOSE,
    BLOG_BLOCK_OPEN,
    has_blog_block,
    insert_or_replace_blog_block,
    render_blog_block,
)
from lib.crew.products.blog_selection import select_products_for_existing_post
from lib.crew.products.brief_from_post import synthesize_brief
from lib.crew.products.discovery import load_discovered_products
from lib.crew.products.models import ProductSelection, SelectedProduct
from lib.crew.products.pool import load_candidate_pool
from lib.crew.products.selector import (
    MAX_PRODUCTS,
    SelectorExecuteFn,
    select_products_for_post,
)
from lib.crew.products.usage import recently_used_keys, record_usage

__all__ = [
    "BLOG_BLOCK_CLOSE",
    "BLOG_BLOCK_OPEN",
    "MAX_PRODUCTS",
    "ProductSelection",
    "SelectedProduct",
    "SelectorExecuteFn",
    "has_blog_block",
    "insert_or_replace_blog_block",
    "load_candidate_pool",
    "load_discovered_products",
    "recently_used_keys",
    "record_usage",
    "render_blog_block",
    "select_products_for_existing_post",
    "select_products_for_post",
    "synthesize_brief",
]
