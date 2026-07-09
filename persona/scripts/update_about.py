"""Deploy the your-brand.com About Me page as clean HTML+CSS.

Replaces the Elementor-controlled page with a fully self-contained layout
matching the homepage design system: Fraunces + DM Sans, coral accents,
warm parchment sections. Looks up the page by slug at run-time.

Usage:
    python scripts/update_about.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.sessions.wp_client import wp_client  # noqa: E402
from about_template import build_html  # noqa: E402

_SETTINGS_PATH = Path(__file__).parent.parent.parent / ".claude" / "settings.local.json"
_PAGE_SLUG = "about-me"


def load_credentials() -> None:
    raw = json.loads(_SETTINGS_PATH.read_text())
    for key in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"):
        if key in (env := raw.get("env", {})):
            os.environ[key] = env[key]


def get_page_id(client) -> int:
    resp = client.get(
        "/wp-json/wp/v2/pages",
        params={"slug": _PAGE_SLUG, "_fields": "id,slug,link"},
    )
    resp.raise_for_status()
    pages = resp.json()
    if not pages:
        raise RuntimeError(f"No WordPress page found with slug '{_PAGE_SLUG}'")
    page_id: int = pages[0]["id"]
    print(f"Found page: ID {page_id}  ({pages[0].get('link', '')})")
    return page_id


def deploy(html: str, page_id: int) -> None:
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
        resp = client.patch(f"/wp-json/wp/v2/pages/{page_id}", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"PATCH /pages/{page_id} failed: {resp.status_code} {resp.text[:400]}"
            )
        url = resp.json().get("link", f"(page {page_id})")
        print(f"About page deployed — {len(html):,} bytes")
        print(f"Live URL: {url}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy the About Me page to WordPress.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_credentials()

    html = build_html()
    print(f"Built HTML: {len(html):,} bytes")

    if args.dry_run:
        print(html[:4000])
        print("\n(dry-run — not deployed)")
        return

    with wp_client() as client:
        page_id = get_page_id(client)

    deploy(html, page_id)


if __name__ == "__main__":
    main()
