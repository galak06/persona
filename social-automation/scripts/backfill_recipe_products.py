"""Backfill the "Our Pick: Tools Used in This Recipe" block into existing
recipe posts on dogfoodandfun.com.

Idempotent via the recipe-tools-block:v1 marker — re-running replaces in place,
never duplicates. Default mode is dry-run; pass --commit to actually PATCH.
Each post requires terminal y/N confirmation in --commit mode.

Handles Elementor-managed posts per feedback_elementor_content_update.md:
clears Elementor meta, strips Gutenberg block comments, saves clean HTML.

Usage:
    python scripts/backfill_recipe_products.py                       # dry-run all
    python scripts/backfill_recipe_products.py --only chicken-bone-broth-for-dogs
    python scripts/backfill_recipe_products.py --commit               # live, with prompts
    python scripts/backfill_recipe_products.py --commit --yes         # live, no prompts
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path
from typing import Final

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.bootstrap import init_script
settings, log = init_script(__name__)

from lib.local_env import load_local_env  # noqa: E402
load_local_env()

from lib.recipe_products import (  # noqa: E402
    insert_or_replace_block,
    load_catalog,
    pick_products,
    render_block,
)
from lib.sessions import wp_client  # noqa: E402

# Slug heuristics — words that strongly indicate "this is a recipe post"
_RECIPE_SLUG_TOKENS: Final[tuple[str, ...]] = (
    "-recipe",
    "biscuit",
    "cookie",
    "stew",
    "broth",
    "frozen-bites",
    "soup",
    "treat",
    "cake",
    "meatball",
    "fries",
    "salmon",
)

# Gutenberg block comments — must be stripped before saving (bloating bug)
_GUTENBERG_COMMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"<!--\s*/?wp:[^>]*-->",
)


@dataclass
class RecipeUpdate:
    post_id: int
    slug: str
    title: str
    elementor_managed: bool
    original_content: str
    new_content: str
    products_picked: list[str]


def _is_recipe_slug(slug: str) -> bool:
    return any(token in slug for token in _RECIPE_SLUG_TOKENS)


def _strip_gutenberg_comments(html: str) -> str:
    return _GUTENBERG_COMMENT_RE.sub("", html)


def _fetch_all_published_posts(client) -> list[dict]:
    posts: list[dict] = []
    page = 1
    while True:
        r = client.get(
            "/wp-json/wp/v2/posts",
            params={
                "per_page": 100,
                "page": page,
                "context": "edit",
                "_fields": "id,slug,title,content,meta,status",
                "status": "publish",
            },
        )
        if r.status_code == 400:
            break  # past the last page
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        posts.extend(batch)
        page += 1
    return posts


def _meta_value(meta: dict | list, key: str) -> str:
    if isinstance(meta, dict):
        return str(meta.get(key, "") or "")
    if isinstance(meta, list):
        for entry in meta:
            if isinstance(entry, dict) and entry.get("key") == key:
                return str(entry.get("value", "") or "")
    return ""


def _is_elementor_managed(post: dict) -> bool:
    meta = post.get("meta", {}) or {}
    return _meta_value(meta, "_elementor_edit_mode").lower() == "builder"


def _build_update(post: dict, catalog, associates_tag: str) -> RecipeUpdate | None:
    slug: str = post["slug"]
    title: str = post.get("title", {}).get("rendered", "") or post.get("title", {}).get("raw", "")
    raw_content = post.get("content", {})
    body: str = raw_content.get("raw") if isinstance(raw_content, dict) else ""
    if not body:
        body = raw_content.get("rendered", "") if isinstance(raw_content, dict) else ""
    if not body:
        return None

    products = pick_products(slug, title, catalog, limit=3)
    if not products:
        return None

    block = render_block(products, slug, associates_tag=associates_tag)
    new_body = insert_or_replace_block(body, block)
    new_body = _strip_gutenberg_comments(new_body)

    if new_body == body:
        return None

    return RecipeUpdate(
        post_id=int(post["id"]),
        slug=slug,
        title=title,
        elementor_managed=_is_elementor_managed(post),
        original_content=body,
        new_content=new_body,
        products_picked=[p.key for p in products],
    )


def _print_diff(update: RecipeUpdate, max_lines: int = 80) -> None:
    diff = list(
        unified_diff(
            update.original_content.splitlines(keepends=False),
            update.new_content.splitlines(keepends=False),
            fromfile=f"{update.slug} (current)",
            tofile=f"{update.slug} (proposed)",
            lineterm="",
            n=2,
        )
    )
    print("\n  --- diff (truncated) ---")
    for line in diff[:max_lines]:
        prefix = "    "
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            prefix = "    "
        print(prefix + line)
    if len(diff) > max_lines:
        print(f"    ... ({len(diff) - max_lines} more diff lines truncated)")


def _commit_update(client, update: RecipeUpdate) -> None:
    payload: dict = {"content": update.new_content}
    if update.elementor_managed:
        # Per feedback_elementor_content_update.md: clear Elementor meta so WP
        # renders from post_content instead of cached _elementor_data.
        payload["meta"] = {
            "_elementor_edit_mode": "",
            "_elementor_template_type": "",
            "_elementor_data": "[]",
        }
    r = client.post(f"/wp-json/wp/v2/posts/{update.post_id}", json=payload)
    r.raise_for_status()


def _confirm(prompt: str, default_yes: bool = False) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    try:
        ans = input(f"{prompt} {suffix} ").strip().lower()
    except EOFError:
        return False
    if not ans:
        return default_yes
    return ans in ("y", "yes")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Our Pick block into recipe posts")
    parser.add_argument("--only", help="process a single post by slug")
    parser.add_argument("--commit", action="store_true", help="actually PATCH the posts")
    parser.add_argument("--yes", action="store_true", help="skip per-post confirmation prompt")
    parser.add_argument("--limit", type=int, default=None, help="cap number of posts processed")
    args = parser.parse_args()

    load_local_env()  # load .claude/settings.local.json env into os.environ

    associates_tag = os.environ.get("AMAZON_ASSOCIATES_TAG", "").strip()
    if not associates_tag:
        print("ERROR: AMAZON_ASSOCIATES_TAG not set in .claude/settings.local.json env")
        return 1

    catalog = load_catalog()

    with wp_client() as client:
        posts = _fetch_all_published_posts(client)
        if args.only:
            posts = [p for p in posts if p["slug"] == args.only]
            if not posts:
                print(f"No published post matched slug '{args.only}'")
                return 1
        else:
            posts = [p for p in posts if _is_recipe_slug(p["slug"])]

        print(f"Candidate recipe posts: {len(posts)}")
        if args.limit:
            posts = posts[: args.limit]

        updates: list[RecipeUpdate] = []
        for post in posts:
            update = _build_update(post, catalog, associates_tag)
            if update is None:
                print(f"  SKIP {post['slug']} — no products matched or no diff")
                continue
            updates.append(update)

        if not updates:
            print("\nNothing to update.")
            return 0

        print(f"\n{len(updates)} post(s) would change:\n")
        for u in updates:
            tag = " [ELEMENTOR]" if u.elementor_managed else ""
            print(f"  • {u.slug}{tag}")
            print(f"    Products: {', '.join(u.products_picked)}")
            _print_diff(u)
            print()

        if not args.commit:
            print("(dry-run — pass --commit to PATCH)")
            return 0

        committed = 0
        for u in updates:
            if not args.yes and not _confirm(
                f"\nPATCH {u.slug} (post_id={u.post_id})?", default_yes=False
            ):
                print(f"  ⏭  skipped {u.slug}")
                continue
            try:
                _commit_update(client, u)
                committed += 1
                print(f"  ✅ patched {u.slug}")
            except Exception as exc:
                print(f"  ❌ failed {u.slug}: {exc}")
        print(f"\nCommitted: {committed}/{len(updates)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
