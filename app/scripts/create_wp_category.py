"""Create one WordPress category for a brand, idempotently.

Adding a category is purely additive: no existing post is recategorised, no
URL changes, and a hand-curated homepage block does not gain a card unless
someone edits it. That is what makes a niche content cluster (a six-month
dental focus, say) possible without restructuring a live site.

Idempotent by slug -- re-running reports the existing category instead of
creating a duplicate, so it is safe in a retry loop or a provisioning script.

    python scripts/create_wp_category.py --name "Dental Care" --slug dental-care
    python scripts/create_wp_category.py --name "Dental Care" --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from lib.local_env import load_brand_env_into_environ, load_local_env
from lib.observability import get_logger
from lib.sessions.wp_client import wp_client

logger = get_logger(__name__)


def _infer_brand_dir() -> Path:
    """`$BRAND_DIR`, else the sole folder under `brands/` if exactly one exists."""
    brand_dir_env = os.environ.get("BRAND_DIR")
    if brand_dir_env:
        return Path(brand_dir_env)
    brands_root = _ENGINE_ROOT / "brands"
    candidates = sorted(
        d for d in brands_root.glob("*") if d.is_dir() and not d.name.startswith((".", "_"))
    )
    if len(candidates) == 1:
        return candidates[0]
    raise SystemExit(
        "BRAND_DIR not set and brands/ doesn't have exactly one brand folder -- pass --brand-dir"
    )


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help='category display name, e.g. "Dental Care"')
    parser.add_argument("--slug", default=None, help="URL slug (default: derived from --name)")
    parser.add_argument("--description", default="", help="category description")
    parser.add_argument("--brand-dir", type=Path, default=None)
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would happen, create nothing"
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    brand_dir = (args.brand_dir or _infer_brand_dir()).resolve()
    load_brand_env_into_environ(brand_dir)
    load_local_env()

    slug = args.slug or _slugify(args.name)

    with wp_client() as client:
        existing = client.get(
            "/wp-json/wp/v2/categories",
            params={"slug": slug, "_fields": "id,name,slug,count", "hide_empty": "false"},
        )
        if existing.status_code == 200 and existing.json():
            found = existing.json()[0]
            print(f"already exists: id={found['id']} {found['name']} ({found['slug']})")
            return 0

        if args.dry_run:
            print(f"dry-run: would create '{args.name}' (slug: {slug})")
            return 0

        resp = client.post(
            "/wp-json/wp/v2/categories",
            json={"name": args.name, "slug": slug, "description": args.description},
        )
        if resp.status_code not in (200, 201):
            print(f"ERROR: HTTP {resp.status_code} -- {resp.text[:300]}", file=sys.stderr)
            return 1

        created = resp.json()
        print(f"created: id={created['id']} {created['name']} -> {created.get('link', '')}")
        logger.info(
            "wp_category_created",
            brand_id=brand_dir.name,
            category_id=created["id"],
            slug=created["slug"],
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
