"""ONE-TIME backfill: give every published blog post a relevant Amazon block.

Recipe posts already get an affiliate block at publish time
(`lib.recipe_products.block_renderer`); ordinary blog posts published
before the crew product-selector stage existed have none. This sweep walks
every PUBLISHED POST (never a page), reconstructs the post's brief from its
own title/slug/`<h2>` structure, asks the existing ProductSelector agent
which catalog products a reader of THAT post would actually want, and
inserts a marker-wrapped, idempotent picks block.

Safety posture, in order of how much damage each prevents:

  * **Elementor guard.** A post whose `_elementor_data` / `_elementor_edit_mode`
    meta is non-empty is skipped outright -- writing `post_content` on an
    Elementor-built post is invisible at best and destructive at worst.
  * **No selector fallback.** A failed LLM call skips the post
    (`lib.crew.products.blog_selection` returns `None`); it never degrades
    to "just staple the flat catalog on", which would put GPS collars on
    unrelated posts.
  * **Backup before every write.** The original raw content is written
    atomically under `--backup-dir` BEFORE the post is updated.
  * **Never sends `status`.** The update payload carries content + the
    Elementor-clearing meta only, so publish state cannot change.
  * **Recipe posts are left alone** unless `--include-recipe-blocks`, and
    even then the refresh happens BETWEEN THE EXISTING RECIPE MARKERS with
    the recipe wording, so those posts read exactly as before.

Credentials (`WP_URL`, `WP_USER`, `WP_APP_PASSWORD`, `AMAZON_ASSOCIATES_TAG`,
`DEEPSEEK_API_KEY`) come from the brand `.env` + `.claude/settings.local.json`
via `lib.local_env` -- never inlined.

Run::

    python -m scripts.backfill_blog_product_blocks --dry-run
    python -m scripts.backfill_blog_product_blocks --only some-slug --limit 1
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.affiliate_resolver import ProductEntry
from lib.crew.products.blog_backfill import (
    MODE_INSERT,
    MODE_SKIP_RECIPE,
    WpPost,
    fetch_posts_by_id,
    fetch_published_posts,
    is_elementor,
    mode_for,
    render_for_mode,
    write_backup,
)
from lib.crew.products.blog_selection import select_products_for_existing_post
from lib.crew.products.brief_from_post import synthesize_brief
from lib.crew.products.pool import load_candidate_pool
from lib.crew.products.selector import SelectorExecuteFn
from lib.crew.products.usage import SOURCE_BACKFILL, recently_used_keys, record_usage
from lib.crew.writer.models import ContentBrief
from lib.local_env import load_brand_env_into_environ, load_local_env
from lib.observability import configure_logging, get_logger

logger = get_logger(__name__)

# Mirror the create path: keep Elementor from hijacking the stored HTML body
# (copied from recipe-publisher/scripts/update_wp_affiliate_blocks.py).
_ELEMENTOR_CLEAR_META = {
    "_elementor_edit_mode": "",
    "_elementor_template_type": "",
    "_elementor_version": "",
    "_elementor_data": "",
    "_elementor_css": "",
    "_elementor_page_assets": "",
}

_BACKUP_RELATIVE = Path("backups") / "blog_product_blocks"


@dataclass
class Sweep:
    """Everything one backfill run needs, plus its mutable in-run state."""

    client: httpx.Client
    pool: dict[str, ProductEntry]
    tag: str
    backup_dir: Path
    dry_run: bool = False
    include_recipe_blocks: bool = False
    execute_fn: SelectorExecuteFn | None = None
    usage_path: Path | None = None
    brand_dir: Path | None = None
    #: Search live for products per post instead of ranking only the static
    #: catalogs. Off by default: it costs a DeepSeek call plus Serper searches
    #: per post, which a whole-site sweep multiplies.
    discover: bool = False
    #: How many times each product key has been placed -- biases candidate
    #: ordering so products get spread around. SEEDED from the usage file
    #: rather than starting empty: as a per-invocation counter it had no
    #: memory across runs, so filling one post and then another in a separate
    #: command reused the same products (live: B0CFBTQRD2 and B07WLDRLDY
    #: landed in both post 4289 and post 4295). Note this only REORDERS
    #: candidates (`lib.crew.products.blog_selection`), it never excludes --
    #: a repeat stays possible when it is genuinely the best fit.
    usage: Counter[str] = field(default_factory=Counter)


def _client() -> httpx.Client:
    base = os.environ["WP_URL"].rstrip("/")
    return httpx.Client(
        base_url=base,
        auth=(os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"]),
        timeout=60.0,
        headers={"User-Agent": "blog-product-backfill/0.1"},
    )


def _report_dry_run(post: WpPost, mode: str, products: list[ProductEntry], block: str) -> None:
    print(f"\n--- post {post.id} | {post.slug} | mode={mode}")
    for product in products:
        print(f"    {product.key}: {product.display}")
    print(block)


def process_post(post: WpPost, sweep: Sweep) -> str:
    """Backfill one post. Returns its status (first token = tally key)."""
    if is_elementor(post.meta):
        logger.info("blog_backfill_skip_elementor", post_id=post.id, slug=post.slug)
        return "skip-elementor"
    mode = mode_for(post.content, include_recipe_blocks=sweep.include_recipe_blocks)
    if mode == MODE_SKIP_RECIPE:
        return MODE_SKIP_RECIPE

    brief = synthesize_brief(title=post.title, slug=post.slug, content_html=post.content)
    pool = sweep.pool
    if sweep.discover and sweep.brand_dir is not None:
        # Curated entries spread LAST so they win any key collision -- they
        # carry real editorial notes where a discovered entry has only a SERP
        # snippet (same precedence as `selector.select_products_for_post`).
        pool = {**_discover_for_post(sweep.brand_dir, brief), **sweep.pool}
    picked = select_products_for_existing_post(
        pool, brief, usage_counts=sweep.usage, execute_fn=sweep.execute_fn
    )
    if picked is None:
        return "skip-selector-failed"
    if not picked:
        return "skip-no-fit"
    sweep.usage.update(picked.keys())

    products = list(picked.values())
    block, new_content = render_for_mode(post, products, mode, sweep.tag)
    if sweep.dry_run:
        _report_dry_run(post, mode, products, block)
    if new_content == post.content:
        return "unchanged"
    if sweep.dry_run:
        return f"would-update (post {post.id}, {mode})"

    write_backup(sweep.backup_dir / f"{post.id}-{post.slug}.html", post.content)
    resp = sweep.client.post(
        f"/wp-json/wp/v2/posts/{post.id}",
        json={"content": new_content, "meta": _ELEMENTOR_CLEAR_META},
    )
    if resp.status_code >= 400:
        logger.error("blog_backfill_update_failed", post_id=post.id, status=resp.status_code)
        return f"FAILED {resp.status_code}: {resp.text[:160]}"
    record_usage(post.slug, list(picked), path=sweep.usage_path, source=SOURCE_BACKFILL)
    logger.info("blog_backfill_updated", post_id=post.id, slug=post.slug, keys=list(picked))
    return f"inserted (post {post.id})" if mode == MODE_INSERT else f"refreshed (post {post.id})"


def _resolve_backup_dir(raw: str | None) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if raw:
        return Path(raw) / stamp
    brand_dir = os.environ.get("BRAND_DIR")
    if not brand_dir:
        raise SystemExit("BRAND_DIR unset and --backup-dir not given — refusing to write blind")
    return Path(brand_dir) / _BACKUP_RELATIVE / stamp


def _discover_for_post(brand_dir: Path, brief: ContentBrief) -> dict[str, ProductEntry]:
    """Live product search for one post. `{}` (logged) on any failure --
    discovery is an enhancement, never a reason to skip repairing a post."""
    from lib.crew.products.discovery import discover_products
    from lib.crew.products.discovery_queries import shopping_queries_for_brief

    try:
        return discover_products(brand_dir, shopping_queries_for_brief(brief))
    except Exception as exc:
        logger.warning("blog_backfill_discovery_failed", slug=brief.suggested_title, error=str(exc))
        return {}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill blog affiliate product blocks.")
    parser.add_argument("--dry-run", action="store_true", help="report changes, write nothing")
    parser.add_argument("--only", help="process a single post slug")
    parser.add_argument(
        "--post-id",
        type=int,
        action="append",
        dest="post_ids",
        help="process these post ids at ANY status (drafts included); repeatable. "
        "The sweep is publish-only by design -- this is how a not-yet-published "
        "draft gets its product block before it faces readers.",
    )
    parser.add_argument("--limit", type=int, help="process at most N posts")
    parser.add_argument(
        "--include-recipe-blocks",
        action="store_true",
        help="also refresh posts that already carry a recipe block (in place)",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="search Amazon live per post (Serper) instead of ranking only the "
        "static catalogs -- widens a 31-product pool that otherwise repeats itself",
    )
    parser.add_argument("--backup-dir", help="where original post HTML is saved before writes")
    parser.add_argument("--log-level", default="INFO", help="DEBUG | INFO | WARNING | ERROR")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, execute_fn: SelectorExecuteFn | None = None) -> int:
    args = _parse_args(argv)
    configure_logging(level=args.log_level)
    load_brand_env_into_environ()
    load_local_env()

    tag = os.environ.get("AMAZON_ASSOCIATES_TAG", "").strip()
    if not tag:
        logger.error("blog_backfill_missing_associates_tag")
        return 1
    brand_dir = Path(os.environ["BRAND_DIR"])
    pool = load_candidate_pool(brand_dir)
    if not pool:
        logger.error("blog_backfill_empty_pool", brand_dir=str(brand_dir))
        return 1

    counts: Counter[str] = Counter()
    with _client() as client:
        if args.post_ids:
            posts = fetch_posts_by_id(client, args.post_ids)
        else:
            posts = fetch_published_posts(client, slug=args.only)
        if args.limit:
            posts = posts[: args.limit]
        scope = "post(s) by id" if args.post_ids else "published post(s)"
        print(f"{'DRY-RUN: ' if args.dry_run else ''}processing {len(posts)} {scope}\n")
        sweep = Sweep(
            brand_dir=brand_dir,
            discover=args.discover,
            usage=Counter(dict.fromkeys(recently_used_keys(), 1)),
            client=client,
            pool=pool,
            tag=tag,
            backup_dir=_resolve_backup_dir(args.backup_dir),
            dry_run=args.dry_run,
            include_recipe_blocks=args.include_recipe_blocks,
            execute_fn=execute_fn,
        )
        for post in posts:
            status = process_post(post, sweep)
            counts[status.split()[0]] += 1
            print(f"  {post.slug[:56]:<56} -> {status}")

    print("\nsummary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 1 if counts.get("FAILED") else 0


if __name__ == "__main__":
    raise SystemExit(main())
