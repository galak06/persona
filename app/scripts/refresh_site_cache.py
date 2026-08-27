"""Refresh `data/cache/site_content_cache.json` from the brand's WP REST API.

The cache grounds internal-link choice, reply drafting and the GSC scout, but
until this script existed nothing in code wrote it -- only the `site-analyzer`
skill, as agent choreography, which left it stale and far shorter than the
brand's own `content_analysis.site_cache_max_posts` allows. A post can only
link to something already in this file, so refreshing it is what lets a
focused category's posts actually reference each other.

    python -m scripts.refresh_site_cache --brand-dir brands/acme
    python -m scripts.refresh_site_cache --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from lib.local_env import load_brand_env_into_environ, load_local_env
from lib.observability import get_logger
from lib.sessions.wp_client import wp_client
from lib.site_cache import build_cache, cache_path, fetch_recent_posts, write_cache

logger = get_logger(__name__)

_DEFAULT_MAX_POSTS = 50


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


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand-dir", type=Path, default=None, help="brand folder to refresh")
    parser.add_argument(
        "--max-posts",
        type=int,
        default=None,
        help="override content_analysis.site_cache_max_posts for this run",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and report, but leave the existing cache file untouched",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    brand_dir = (args.brand_dir or _infer_brand_dir()).resolve()
    load_brand_env_into_environ(brand_dir)
    load_local_env()

    config = _read_json(brand_dir / "config.json")
    if not config:
        print(f"ERROR: no readable config.json under {brand_dir}", file=sys.stderr)
        return 1

    content_analysis = config.get("content_analysis") or {}
    max_posts = args.max_posts or int(content_analysis.get("site_cache_max_posts") or 0)
    if max_posts <= 0:
        max_posts = _DEFAULT_MAX_POSTS

    site = config.get("site") or {}
    existing = _read_json(cache_path(brand_dir))
    before = len(existing.get("recent_posts") or [])

    with wp_client() as client:
        posts = fetch_recent_posts(client, max_posts=max_posts)

    if not posts:
        # Never overwrite a populated cache with nothing: a transient REST
        # failure would otherwise strip every internal-link candidate the
        # brand has, which is far worse than serving a stale cache.
        print("ERROR: fetched 0 posts -- leaving the existing cache untouched", file=sys.stderr)
        return 1

    cache = build_cache(
        existing,
        posts,
        site_url=str(site.get("url") or ""),
        site_name=str(site.get("name") or brand_dir.name),
    )

    categories = sorted({c for p in posts for c in p["categories"]})
    print(f"brand        : {brand_dir.name}")
    print(f"cached posts : {before} -> {len(posts)} (cap {max_posts})")
    print(f"categories   : {', '.join(categories) if categories else '(none)'}")

    if args.dry_run:
        print("dry-run      : cache NOT written")
        return 0

    path = write_cache(brand_dir, cache)
    print(f"wrote        : {path}")
    logger.info(
        "site_cache_refreshed",
        brand_id=brand_dir.name,
        posts=len(posts),
        categories=len(categories),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
