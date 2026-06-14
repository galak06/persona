# pyright: reportMissingImports=false
"""Refresh the "Our Pick: Tools Used in This Recipe" affiliate block on every
already-published WordPress recipe post — in place, without creating new posts.

The publish pipeline only ever *creates* posts, so re-running it would duplicate.
This script instead:
  1. reads each recipe that has a ``wp_url`` from the recipe DB,
  2. rebuilds the affiliate block from the recipe's persisted ``affiliate_products``
     (looked up against the catalog for blurbs),
  3. fetches the live post (by the slug in its ``wp_url``) with ``context=edit``,
  4. idempotently inserts-or-replaces the marker-wrapped block, and
  5. PATCHes the post content back only when it actually changed.

WP credentials + AMAZON_ASSOCIATES_TAG come from settings.local.json via
``load_local_env`` (never inlined).

Run::

    python -m scripts.update_wp_affiliate_blocks [--dry-run] [--only SLUG] [--limit N]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("update_wp_affiliate_blocks")

_RECIPE_PUBLISHER = Path(__file__).resolve().parent.parent
_SOCIAL_AUTOMATION = _RECIPE_PUBLISHER.parent
for _root in (_RECIPE_PUBLISHER, _SOCIAL_AUTOMATION):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from recipe_db import db  # noqa: E402
from recipe_db.repository import RecipeRepository  # noqa: E402

from lib.local_env import load_local_env  # noqa: E402
from lib.recipe_products.catalog import RecipeCatalog, RecipeProduct, load_catalog  # noqa: E402
from lib.recipe_products.block_renderer import (  # noqa: E402
    insert_or_replace_block,
    render_block,
)

# Mirror the create path: keep Elementor from hijacking the stored HTML body.
_ELEMENTOR_CLEAR_META = {
    "_elementor_edit_mode": "",
    "_elementor_template_type": "",
    "_elementor_version": "",
    "_elementor_data": "",
    "_elementor_css": "",
    "_elementor_page_assets": "",
}


def _client() -> httpx.Client:
    base = os.environ["WP_URL"].rstrip("/")
    return httpx.Client(
        base_url=base,
        auth=(os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"]),
        timeout=60.0,
        headers={"User-Agent": "recipe-publisher/0.1 (+dogfoodandfun.com)"},
    )


def _slug_from_url(wp_url: str) -> str:
    """Last non-empty path segment of the permalink is the WP post slug."""
    return [seg for seg in urlparse(wp_url).path.split("/") if seg][-1]


def _products_for(entries: list[dict[str, str]], catalog: RecipeCatalog) -> list[RecipeProduct]:
    """Resolve persisted {key,asin,display} entries to full catalog products (for blurbs)."""
    products: list[RecipeProduct] = []
    for entry in entries:
        product = catalog.get(entry["key"])
        if product is not None:
            products.append(product)
    return products


def _fetch_post(client: httpx.Client, slug: str) -> tuple[int, str] | None:
    """Return (post_id, raw_content) for a published post by slug, or None."""
    resp = client.get(
        "/wp-json/wp/v2/posts",
        params={"slug": slug, "context": "edit", "status": "publish,draft,private"},
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    post = rows[0]
    return int(post["id"]), post["content"]["raw"]


def _update_one(
    client: httpx.Client,
    recipe_id: str,
    wp_url: str,
    entries: list[dict[str, str]],
    catalog: RecipeCatalog,
    tag: str,
    *,
    dry_run: bool,
) -> str:
    products = _products_for(entries, catalog)
    if not products:
        return "no-products"
    block = render_block(products, recipe_id, associates_tag=tag)

    slug = _slug_from_url(wp_url)
    found = _fetch_post(client, slug)
    if found is None:
        return f"NOT-FOUND (slug={slug})"
    post_id, raw = found

    new_content = insert_or_replace_block(raw, block)
    if new_content == raw:
        return "unchanged"
    if dry_run:
        return f"would-update (post {post_id})"

    patch = client.post(
        f"/wp-json/wp/v2/posts/{post_id}",
        json={"content": new_content, "meta": _ELEMENTOR_CLEAR_META},
    )
    if patch.status_code >= 400:
        return f"FAILED {patch.status_code}: {patch.text[:160]}"
    return f"updated (post {post_id})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh WP affiliate blocks in place.")
    parser.add_argument("--dry-run", action="store_true", help="report changes without PATCHing")
    parser.add_argument("--only", help="process a single recipe id")
    parser.add_argument("--limit", type=int, help="process at most N recipes")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    load_local_env()
    tag = os.environ.get("AMAZON_ASSOCIATES_TAG", "").strip()
    if not tag:
        logger.error("AMAZON_ASSOCIATES_TAG not set — refusing to render untagged links")
        return 1

    catalog = load_catalog()
    conn = db.connect()
    try:
        db.migrate(conn)
        repo = RecipeRepository(conn)
        recipes = [r for r in repo.list_recipes() if r.wp_url]
    finally:
        conn.close()

    if args.only:
        recipes = [r for r in recipes if r.id == args.only]
    if args.limit:
        recipes = recipes[: args.limit]

    prefix = "DRY-RUN: " if args.dry_run else ""
    logger.info("%sprocessing %d published recipe(s)\n", prefix, len(recipes))
    counts: dict[str, int] = {}
    with _client() as client:
        for r in recipes:
            status = _update_one(
                client, r.id, r.wp_url, r.affiliate_products, catalog, tag, dry_run=args.dry_run
            )
            key = status.split()[0]
            counts[key] = counts.get(key, 0) + 1
            logger.info("  %-48s -> %s", r.id, status)

    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    logger.info("\nsummary: %s", summary)
    failed = any(k in ("FAILED", "NOT-FOUND") for k in counts)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
