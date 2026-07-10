#!/usr/bin/env python3
"""Generate and upload recipe card PDFs for published posts.

Usage:
    python generate_recipe_card.py --post-id 1234
    python generate_recipe_card.py --post-url https://yourbrand.com/some-recipe/
    python generate_recipe_card.py --scan-new
    python generate_recipe_card.py --scan-new --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.bootstrap import init_script  # type: ignore[import-untyped]

_app, log = init_script(__name__)

from lib.local_env import load_local_env  # type: ignore[import-untyped]

load_local_env()

from lib.recipe_card import content_parser, pdf_generator, wp_sync  # type: ignore[import-untyped]
from lib.sessions.wp_client import wp_client  # type: ignore[import-untyped]

_DOWNLOAD_BUTTON_MARKER = "recipe-card-download"


def process_post(post_id: int, dry_run: bool, force: bool = False) -> bool:
    """Run the full recipe card pipeline for a single post.

    Args:
        post_id: WordPress post ID.
        dry_run: When True, prints what would be processed without writing anything.
        force: When True, regenerate even if the download button already exists.

    Returns:
        True on success or skip (button already present); False on error.
    """
    rc = _app.recipe_card

    try:
        post = wp_sync.fetch_post_data(post_id)
    except Exception as exc:
        log.error("Failed to fetch post %d: %s", post_id, exc)
        return False

    title: str = post["title"]
    content: str = post["content"]
    slug: str = post["slug"]

    recipe = content_parser.parse_recipe(title, content)

    if dry_run:
        log.info(
            "[dry-run] post=%d slug=%r title=%r ingredients=%d instructions=%d",
            post_id,
            slug,
            title[:60],
            len(recipe.ingredients),
            len(recipe.instructions),
        )
        return True

    if not recipe.ingredients and not recipe.instructions:
        log.warning(
            "Post %d (%r) has no parseable recipe content — skipping.", post_id, slug
        )
        return False

    if _DOWNLOAD_BUTTON_MARKER in content and not force:
        log.info("Post %d already has download button — skipping.", post_id)
        return True

    if _DOWNLOAD_BUTTON_MARKER in content and force:
        log.info("Post %d: --force set, removing old button before regenerating.", post_id)
        wp_sync.remove_download_button(post_id)

    try:
        stamp = wp_sync.fetch_nalla_stamp(rc.stamp_media_id)
        pdf_bytes = pdf_generator.generate_recipe_card_pdf(
            title=recipe.title,
            ingredients=recipe.ingredients,
            instructions=recipe.instructions,
            nalla_stamp_bytes=stamp,
            cook_temp=recipe.cook_temp,
            cook_time=recipe.cook_time,
            header_title=rc.header_title,
            footer_text=rc.footer_text,
        )
        filename = f"recipe-card-{slug}.pdf"
        pdf_url = wp_sync.upload_pdf(pdf_bytes, filename)
        wp_sync.inject_download_button(post_id, pdf_url)
    except Exception as exc:
        log.error("Pipeline error for post %d (%r): %s", post_id, slug, exc)
        return False

    log.info("Recipe card published for post %d (%r) → %s", post_id, slug, pdf_url)
    return True


def resolve_post_id_from_url(url: str) -> int:
    """Resolve a WordPress post URL to its integer post ID.

    Args:
        url: Full post URL, e.g. ``https://yourbrand.com/some-recipe/``.

    Returns:
        WordPress post ID.

    Raises:
        ValueError: If the slug cannot be resolved or the post is not found.
    """
    slug = urlparse(url.rstrip("/")).path.rstrip("/").split("/")[-1]
    if not slug:
        raise ValueError(f"Could not extract slug from URL: {url!r}")

    with wp_client() as client:
        resp = client.get(
            "/wp-json/wp/v2/posts",
            params={"slug": slug, "_fields": "id,status"},
        )
    resp.raise_for_status()

    posts: list[dict] = resp.json()
    if not posts:
        raise ValueError(f"No published post found for slug {slug!r} (url={url!r})")

    post_id: int = posts[0]["id"]
    log.info("Resolved slug %r → post_id=%d", slug, post_id)
    return post_id


def scan_new_posts(dry_run: bool) -> None:
    """Find all published posts missing the download button and process them.

    Args:
        dry_run: When True, prints what would be processed without writing anything.
    """
    with wp_client() as client:
        resp = client.get(
            "/wp-json/wp/v2/posts",
            params={"status": "publish", "per_page": 100, "_fields": "id,title,content"},
        )
    resp.raise_for_status()

    all_posts: list[dict] = resp.json()
    pending = [
        p for p in all_posts
        if _DOWNLOAD_BUTTON_MARKER not in p.get("content", {}).get("rendered", "")
    ]

    log.info(
        "Found %d published post(s), %d missing recipe card button.",
        len(all_posts),
        len(pending),
    )

    processed = skipped = errors = 0
    for post in pending:
        post_id: int = post["id"]
        ok = process_post(post_id, dry_run)
        if ok:
            processed += 1
        else:
            errors += 1

    log.info(
        "Scan complete — processed=%d skipped=%d errors=%d%s",
        processed,
        skipped,
        errors,
        " (dry-run)" if dry_run else "",
    )


def main() -> None:
    """Parse CLI args and dispatch to the appropriate pipeline function."""
    parser = argparse.ArgumentParser(
        description="Generate and upload recipe card PDFs for published WP posts."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--post-id", type=int, metavar="ID", help="process a single post by ID")
    group.add_argument("--post-url", metavar="URL", help="process a single post by URL")
    group.add_argument(
        "--scan-new",
        action="store_true",
        help="process all published posts missing the download button",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be processed without writing to WordPress",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="regenerate and re-inject even if download button already exists",
    )
    args = parser.parse_args()

    if args.post_id:
        ok = process_post(args.post_id, args.dry_run, force=args.force)
        sys.exit(0 if ok else 1)

    if args.post_url:
        try:
            post_id = resolve_post_id_from_url(args.post_url)
        except ValueError as exc:
            log.error("Could not resolve URL: %s", exc)
            sys.exit(1)
        ok = process_post(post_id, args.dry_run, force=args.force)
        sys.exit(0 if ok else 1)

    if args.scan_new:
        scan_new_posts(args.dry_run)


if __name__ == "__main__":
    main()
