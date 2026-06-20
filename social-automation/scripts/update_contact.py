"""Deploy the dogfoodandfun.com Contact page as clean HTML+CSS.

Replaces the Elementor-controlled page (ID 2473) with a fully self-contained
layout matching the homepage design system. The existing CF7 form (id=2) is
preserved via a wp:shortcode block embedded between the intro and FAQ sections.

Usage:
    python scripts/update_contact.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.sessions.wp_client import wp_client  # noqa: E402
from contact_template import build_html  # noqa: E402

_SETTINGS_PATH = Path(__file__).parent.parent.parent / ".claude" / "settings.local.json"
_PAGE_ID = 2473


def load_credentials() -> None:
    raw = json.loads(_SETTINGS_PATH.read_text())
    for key in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"):
        if key in (env := raw.get("env", {})):
            os.environ[key] = env[key]


def deploy(html: str) -> None:
    payload = {
        "content": html,
        "status": "publish",
        "comment_status": "open",
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
        print(f"Contact page deployed — {len(html):,} bytes")
        print(f"Live URL: {url}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy the Contact page to WordPress.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_credentials()

    html = build_html()
    print(f"Built HTML: {len(html):,} bytes")

    if args.dry_run:
        print(html[:4000])
        print("\n(dry-run — not deployed)")
        return

    deploy(html)


if __name__ == "__main__":
    main()
