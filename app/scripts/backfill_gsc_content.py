"""Backfill `published_content` with GSC query data for a brand's published posts.

For every post in `site-analyzer`'s cache (`data/cache/site_content_cache.json`,
falling back to the pre-2026-06-reorg `data/site_content_cache.json` --
see `lib.gsc_scout.load_site_content_cache`), pulls Search Analytics rows
from Google Search Console (`lib.gsc_client`) and upserts one
`published_content` row per (post URL, GSC query) pair via
`lib.published_content_db`. Then runs the opportunity scout
(`lib.gsc_scout.run_scout`) so a real backfill also produces real
`content_ideas` rows in the same run -- the plan's own Slice 2 sequencing
(backfill first, since the cannibalization check is meaningless against an
empty table; scout second, consuming what was just backfilled).

**Known scope limit:** the site-content cache is capped at
`content_analysis.site_cache_max_posts` (50 for dogfoodandfun today) most
RECENT posts, not full site history -- despite the plan's "every
already-published URL" phrasing, that full list isn't available anywhere in
committed code today (`api/recipes_api.py`'s local SQLite `recipes.db` is
explicitly out of scope per the plan's blocker 4, and is stale/out-of-sync
regardless). This backfills what the cache actually holds; a full-history
source is a follow-up, not something this script invents.

**Known field gap:** the cache has no numeric WordPress post ID (only
title/url/tags/excerpt), so every row's `wp_post_id` is written empty.

**Data-sufficiency gate** (`lib.gsc_data_sufficiency`): after backfilling,
checks whether this brand has >=15 distinct queries in the position 6-25
band with >=40 impressions. Brands that don't clear it are logged clearly
so scout output is understood as topic *discovery*, not ranking
*optimization*, until more GSC history accumulates.

**Human prerequisite, not automatable by this script:** a GCP service
account must exist, have the Search Console API enabled, and be added as a
verified user on this brand's Search Console property, with its key file
at `$BRAND_DIR/.env`'s `GOOGLE_SERVICE_ACCOUNT_PATH`. Until that's done,
`lib.gsc_client.fetch_search_analytics` raises before any network call --
this script is written and tested against mocked API responses regardless.

Usage::

    python -m scripts.backfill_gsc_content --dry-run
    python -m scripts.backfill_gsc_content --apply
    python -m scripts.backfill_gsc_content --apply --days 180 --skip-scout
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from lib.gsc_client import DEFAULT_TARGET_COUNTRIES, fetch_search_analytics, resolve_site_url
from lib.gsc_data_sufficiency import evaluate_data_sufficiency
from lib.gsc_scout import load_site_content_cache, run_scout
from lib.local_env import load_brand_env_into_environ, load_local_env
from lib.observability import get_logger
from lib.published_content_db.repository import PublishedContentRepository

logger = get_logger(__name__)


def _infer_brand_dir() -> Path:
    """`$BRAND_DIR`, else the sole folder under `brands/` if exactly one exists.

    Deliberately reimplemented rather than imported from
    `lib.config.default_brand_dir` -- importing `lib.config` at all runs its
    module-level `settings = load_config()` singleton immediately, which
    requires `BRAND_DIR` to already be set AND that brand's `config.json` to
    satisfy the full `AppSettings` schema. This script needs neither just to
    locate the brand folder, so it avoids that import-time landmine.
    """
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


def _normalize_url(url: str) -> str:
    return url.strip().rstrip("/")


def match_query_rows_to_posts(
    gsc_rows: list[Any], recent_posts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """(query, page) rows whose `page` matches a cached post -- everything
    else (category/tag/archive pages, etc.) isn't a "published post" row.

    Returns plain dicts (not `published_content_db`-shaped yet -- caller
    adds `brand_id`/`fetched_at`) so this stays a pure, easily testable
    matching function independent of the repository.
    """
    post_urls = {_normalize_url(str(p.get("url", ""))): p for p in recent_posts if p.get("url")}
    matched: list[dict[str, Any]] = []
    for row in gsc_rows:
        post = post_urls.get(_normalize_url(row.page))
        if post is None:
            continue
        matched.append(
            {
                "wp_url": str(post["url"]),
                "gsc_query": row.query,
                "position": row.position,
                "impressions": row.impressions,
                "clicks": row.clicks,
                "ctr": row.ctr,
            }
        )
    return matched


def _resolve_site_property(config: dict[str, Any], override: str | None) -> str:
    """The exact `siteUrl` GSC expects -- resolved via `sites.list()`, not
    guessed from `config.json`'s plain https URL (see `gsc_client.resolve_site_url`
    for why: Domain vs URL-prefix properties use incompatible identifier shapes).
    """
    if override:
        return override
    site_url = str(config.get("site", {}).get("url", "")).strip()
    if not site_url:
        raise SystemExit("no site.url in config.json -- pass --site-url explicitly")
    try:
        return resolve_site_url(site_url)
    except (RuntimeError, FileNotFoundError) as exc:
        raise SystemExit(f"could not resolve GSC property for '{site_url}': {exc}") from None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--brand-dir", type=Path, default=None, help="brand folder (default: $BRAND_DIR)"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="print what would be written")
    group.add_argument("--apply", action="store_true", help="write the rows")
    parser.add_argument(
        "--days", type=int, default=90, help="GSC lookback window in days (default 90)"
    )
    parser.add_argument(
        "--site-url", default=None, help="override the GSC property (default: config.json site.url)"
    )
    parser.add_argument(
        "--skip-scout", action="store_true", help="backfill only; don't run the opportunity scout"
    )
    parser.add_argument(
        "--countries",
        default=None,
        help="comma-separated ISO-3166-1 alpha-3 codes to restrict GSC results to "
        f"(default: {','.join(DEFAULT_TARGET_COUNTRIES)}); pass an empty string for no filter",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    brand_dir = (args.brand_dir or _infer_brand_dir()).resolve()
    load_brand_env_into_environ(brand_dir)
    load_local_env()

    config_path = brand_dir / "config.json"
    if not config_path.is_file():
        print(f"ERROR: no config.json under {brand_dir}", file=sys.stderr)
        return 1

    config = json.loads(config_path.read_text(encoding="utf-8"))
    brand_id = brand_dir.name
    site_url = _resolve_site_property(config, args.site_url)

    recent_posts = list(load_site_content_cache(brand_dir).get("recent_posts", []) or [])
    end_date, start_date = date.today(), date.today() - timedelta(days=args.days)

    if args.countries is None:
        countries = DEFAULT_TARGET_COUNTRIES
    elif args.countries.strip() == "":
        countries = None
    else:
        countries = tuple(c.strip() for c in args.countries.split(",") if c.strip())

    gsc_rows = fetch_search_analytics(
        site_url,
        start_date,
        end_date,
        dimensions=("query", "page"),
        countries=countries,
    )
    matched = match_query_rows_to_posts(gsc_rows, recent_posts)
    sufficiency = evaluate_data_sufficiency(
        [(r["gsc_query"], r["position"], r["impressions"]) for r in matched]
    )

    print(f"brand          : {brand_id} ({site_url})")
    print(
        f"countries      : {', '.join(countries) if countries else '(no filter -- all countries)'}"
    )
    print(f"date range     : {start_date} .. {end_date}")
    print(f"cached posts   : {len(recent_posts)}")
    print(f"GSC rows fetched: {len(gsc_rows)}")
    print(f"matched rows   : {len(matched)} (posts with GSC query data)")
    print(
        f"data sufficiency: {'OK' if sufficiency.sufficient else 'INSUFFICIENT'} "
        f"({sufficiency.qualifying_query_count}/{sufficiency.threshold} qualifying queries "
        f"in position 6-25 band, >=40 impressions)"
    )
    if not sufficiency.sufficient:
        logger.warning(
            "backfill_gsc_content_insufficient_data",
            brand_id=brand_id,
            qualifying_query_count=sufficiency.qualifying_query_count,
            threshold=sufficiency.threshold,
            note="insufficient GSC history -- treat scout output as discovery, not optimization",
        )

    if not args.apply:
        print("\n(dry run -- nothing written; pass --apply to write)")
        if not args.skip_scout:
            run_scout(brand_dir, dry_run=True)
        return 0

    repo = PublishedContentRepository()
    for row in matched:
        repo.upsert({**row, "brand_id": brand_id, "fetched_at": date.today().isoformat()})
    print(f"\nupserted {len(matched)} published_content rows")

    if not args.skip_scout:
        results = run_scout(brand_dir, dry_run=False)
        inserted = sum(1 for _, idea_id in results if idea_id)
        print(f"scout: {len(results)} candidates scored, {inserted} content_ideas inserted")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
