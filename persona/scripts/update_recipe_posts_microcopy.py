"""Batch update all recipe post content to apply microcopy changes.

Changes per post:
  "View on Amazon"              → "Buy on Amazon"   (in <a> link text)
  "Download Recipe Card (PDF)"  → "Save & Print This Recipe"  (button/link text)
  Injects a JS snippet to change the WP comment form submit → "Leave a Note"

Only posts that need changes are patched (idempotent — safe to re-run).

Usage:
    python scripts/update_recipe_posts_microcopy.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.sessions.wp_client import wp_client  # noqa: E402

_SETTINGS_PATH = Path(__file__).parent.parent.parent / ".claude" / "settings.local.json"
_RECIPES_CATEGORY_ID = 41

# Small JS appended once per post — changes WP comment form submit button label.
# Guard prevents double-injection on re-runs.
_COMMENT_BTN_JS = (
    '<script>'
    'if(!window._dffCmtPatched){'
    'window._dffCmtPatched=1;'
    'document.addEventListener("DOMContentLoaded",function(){'
    'var btn=document.getElementById("submit");'
    'if(btn)btn.value="Leave a Note";'
    '});}'
    '</script>'
)


def load_credentials() -> None:
    raw = json.loads(_SETTINGS_PATH.read_text())
    for key in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"):
        if key in (env := raw.get("env", {})):
            os.environ[key] = env[key]


def _apply_replacements(html: str) -> tuple[str, int]:
    """Apply all text replacements to post HTML. Returns (updated_html, change_count)."""
    changes = 0

    # "View on Amazon" → "Buy on Amazon" inside <a> tags
    updated = re.sub(
        r'(<a\b[^>]*>)\s*View on Amazon\s*(</a>)',
        r'\1Buy on Amazon\2',
        html,
        flags=re.IGNORECASE,
    )
    changes += len(re.findall(r'Buy on Amazon', updated)) - len(re.findall(r'Buy on Amazon', html))

    # PDF download button text
    updated = re.sub(
        r'(\U0001F43E\s*)?Download Recipe Card \(PDF\)',
        'Save &amp; Print This Recipe',
        updated,
        flags=re.IGNORECASE,
    )
    if updated != html and changes == 0:
        changes += 1

    # Inject comment form JS if not already present
    if '_dffCmtPatched' not in updated:
        updated = updated + '\n' + _COMMENT_BTN_JS
        changes += 1

    return updated, changes


def fetch_all_recipes(client) -> list[dict]:
    results = []
    page = 1
    while True:
        resp = client.get(
            "/wp-json/wp/v2/posts",
            params={
                "categories": _RECIPES_CATEGORY_ID,
                "per_page": 100,
                "page": page,
                "status": "publish",
                "context": "edit",
                "_fields": "id,title,content",
            },
        )
        if resp.status_code == 400:
            break
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        results.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply microcopy to all recipe posts.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_credentials()

    print("Fetching recipe posts...")
    with wp_client() as client:
        posts = fetch_all_recipes(client)
        print(f"Found {len(posts)} recipe posts.")

        patched = skipped = 0
        for post in posts:
            pid = post["id"]
            title = (post.get("title") or {}).get("rendered", "")[:50]
            raw_html = (post.get("content") or {}).get("raw", "")

            updated, changes = _apply_replacements(raw_html)
            if changes == 0:
                skipped += 1
                continue

            if args.dry_run:
                print(f"  [DRY RUN] {pid} — {title} | {changes} change(s)")
                patched += 1
                continue

            resp = client.patch(
                f"/wp-json/wp/v2/posts/{pid}",
                json={"content": updated},
            )
            if resp.status_code >= 400:
                print(f"  ERROR {pid}: {resp.status_code} {resp.text[:200]}")
                continue

            print(f"  Updated {pid} — {title} | {changes} change(s)")
            patched += 1

        print(f"\nDone: {patched} updated, {skipped} already current.")
        if args.dry_run:
            print("(dry-run — no changes written)")


if __name__ == "__main__":
    main()
