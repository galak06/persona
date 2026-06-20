"""Rebuild the dogfoodandfun.com homepage (page 2101) as clean HTML+CSS+JS.

Replaces the Elementor-controlled page with a fully server-rendered layout:
  Hero · About · Category Grid · Nalla Certified · Blog Grid · Newsletter · How We Review

Blog grid fetches the 3 most-recent non-recipe posts dynamically at deploy time,
so re-running after new posts publish refreshes the section automatically.

Usage:
    python scripts/update_homepage.py [--dry-run]
"""
from __future__ import annotations

import argparse
import html as hl
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.sessions.wp_client import wp_client  # noqa: E402
from homepage_template import build_html  # noqa: E402

_SETTINGS_PATH = Path(__file__).parent.parent.parent / ".claude" / "settings.local.json"
_PAGE_ID = 2101
_RECIPE_CAT_ID = 41


def load_credentials() -> None:
    raw = json.loads(_SETTINGS_PATH.read_text())
    for key in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"):
        if key in (env := raw.get("env", {})):
            os.environ[key] = env[key]


def _strip_tags(text: str) -> str:
    return hl.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _format_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso[:10]).strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return ""


def _get_feat_image(post: dict, client) -> str:
    mid = post.get("featured_media", 0)
    if not mid:
        return ""
    try:
        r = client.get(f"/wp-json/wp/v2/media/{mid}", params={"_fields": "source_url"})
        return r.json().get("source_url", "") if r.status_code == 200 else ""
    except Exception:
        return ""


def fetch_recent_posts(client) -> list[dict]:
    resp = client.get(
        "/wp-json/wp/v2/posts",
        params={
            "per_page": 6,
            "status": "publish",
            "categories_exclude": f"{_RECIPE_CAT_ID},1",
            "_fields": "id,title,excerpt,link,date,featured_media,categories",
            "orderby": "date",
            "order": "desc",
        },
    )
    resp.raise_for_status()
    posts = resp.json()

    # Fetch category names for badges
    cat_ids = {cid for p in posts for cid in p.get("categories", [])}
    cat_map: dict[int, str] = {}
    if cat_ids:
        cr = client.get(
            "/wp-json/wp/v2/categories",
            params={"include": ",".join(str(i) for i in cat_ids), "_fields": "id,name"},
        )
        if cr.status_code == 200:
            cat_map = {c["id"]: hl.unescape(c["name"]) for c in cr.json()}

    result = []
    for post in posts[:3]:
        cats = post.get("categories", [])
        cat_name = cat_map.get(cats[0], "") if cats else ""
        image = _get_feat_image(post, client)
        # Fallback: try fifu meta from content JSON-LD
        if not image:
            content_r = client.get(
                f"/wp-json/wp/v2/posts/{post['id']}",
                params={"context": "edit", "_fields": "meta"},
            )
            if content_r.status_code == 200:
                image = (content_r.json().get("meta") or {}).get("fifu_image_url", "")
        result.append({
            "title": _strip_tags((post.get("title") or {}).get("rendered", "")),
            "excerpt": _strip_tags((post.get("excerpt") or {}).get("rendered", "")),
            "link": post.get("link", ""),
            "date": _format_date(post.get("date", "")),
            "image": image,
            "cat_name": cat_name,
        })
    return result


def deploy(html: str) -> None:
    payload = {
        "content": html,
        "status": "publish",
        "meta": {
            "_elementor_edit_mode": "",
            "_elementor_template_type": "",
            "_elementor_version": "",
            "_elementor_data": "",
            "_elementor_css": "",
            "_elementor_page_assets": "",
        },
    }
    with wp_client() as client:
        resp = client.patch(f"/wp-json/wp/v2/pages/{_PAGE_ID}", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"PATCH /pages/{_PAGE_ID} failed: {resp.status_code} {resp.text[:400]}"
            )
        url = resp.json().get("link", f"(page {_PAGE_ID})")
        print(f"Homepage deployed — {len(html):,} bytes")
        print(f"Live URL: {url}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild homepage as clean HTML+CSS.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_credentials()

    print("Fetching recent blog posts...")
    with wp_client() as client:
        posts = fetch_recent_posts(client)
    print(f"Got {len(posts)} posts: {[p['title'][:40] for p in posts]}")

    html = build_html(posts)
    print(f"Built HTML: {len(html):,} bytes")

    if args.dry_run:
        print(html[:3000])
        print("\n(dry-run — not deployed)")
        return

    deploy(html)


if __name__ == "__main__":
    main()
