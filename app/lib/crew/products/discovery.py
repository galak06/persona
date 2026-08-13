"""Dynamic Amazon product discovery via Serper, for topics the curated
catalog doesn't cover.

`data/config/affiliate_products.json` is hand-maintained, so a post about a
topic nobody has curated products for gets nothing -- the selector is a
ranker over a fixed list, never a finder. This module supplies the finder,
so the pool grows with the content instead of being typed in ahead of it.

**Why Serper rather than Amazon's own API.** The Product Advertising API
needs 3 qualifying sales per 180 days to retain access, which this account
does not have. Serper is already provisioned, already paid for, and already
used by the content scout's Trends agent -- pointing it at
`site:amazon.com` returns the ASIN directly in the result URL. That is a
Google result read through a licensed API, not scraped from Amazon.

**Why the ASIN and title are enough.** `lib.affiliate_resolver.ProductEntry`
requires exactly `key`/`asin`/`display`, and `lib.crew.products.block`
deliberately never renders a price ("Amazon Associates forbids stating a
price that isn't pulled live from their API"). The one thing PA-API is
strictly required for is therefore the one thing this codebase must not
show -- so a SERP result carries everything a linkable product needs.

Discovered entries are cached under `data/cache/` and merged BEHIND the
curated catalog: a hand-written entry always wins a key collision, because
it carries real editorial notes the selector reasons over. Nothing here
raises -- any failure degrades to "no discovered products", which is the
pre-existing behavior.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lib.affiliate_resolver import ProductEntry
from lib.observability import get_logger

logger = get_logger(__name__)

_SERPER_URL = "https://google.serper.dev/search"
_SERPER_TIMEOUT_SECONDS = 20
_CACHE_RELATIVE = Path("data") / "cache" / "discovered_products.json"
_CACHE_TTL_DAYS = 30
_RESULTS_PER_QUERY = 10
_MAX_DISCOVERED = 12
# Amazon ASINs are exactly 10 chars of [A-Z0-9], and always follow /dp/ in a
# product URL. Matching the URL (not the page) is what keeps this a read of
# Google's result set rather than anything touching Amazon.
_ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})")
_KEY_STRIP_RE = re.compile(r"[^a-z0-9]+")
# SERP titles are marketing blurbs ("KYEESE Dog Cooling Vest Evaporative Dog
# Cooler Jacket for Small Medium Large Dogs, Reflective..."). Cut at the
# first separator so `display` reads like a product name, not a keyword dump.
_TITLE_CUT_RE = re.compile(r"\s*[|–—,:(]\s*")
_AMAZON_PREFIX_RE = re.compile(r"^\s*amazon(\.com)?\s*[:\-|]?\s*", re.IGNORECASE)
_MIN_DISPLAY_CHARS = 12
# Department/category titles Google returns for some product URLs. Linking
# these is legal but useless -- the row would name no product.
_GENERIC_DISPLAYS = frozenset(
    {"pet supplies", "dog supplies", "dog food", "pet products", "amazon com", "home kitchen"}
)

SearchFn = Callable[[str, int], list[dict[str, Any]]]


def _serper_search(query: str, num: int) -> list[dict[str, Any]]:
    """One live Serper call. `[]` (logged) on any failure, never raises."""
    api_key = os.environ.get("SERPER_API_KEY", "").strip()
    if not api_key:
        logger.warning("crew_products_discovery_no_api_key")
        return []
    try:
        import httpx

        response = httpx.post(
            _SERPER_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": f"{query} site:amazon.com", "num": num},
            timeout=_SERPER_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            logger.warning("crew_products_discovery_http_error", status=response.status_code)
            return []
        organic = response.json().get("organic", [])
    except Exception as exc:  # network, JSON, httpx -- all non-fatal
        logger.warning("crew_products_discovery_request_failed", error=str(exc))
        return []
    return organic if isinstance(organic, list) else []


def _clean_display(title: str) -> str:
    """A readable product name from a SERP title, or `""` if there isn't one.

    Google prefixes some Amazon results with the store name and titles others
    with only a department ("Pet Supplies", live-observed on B08RC4VR7H). Both
    produce a block row that names no product, so the storefront prefix is
    stripped and anything still too generic is rejected outright -- a link
    labelled "Pet Supplies" is worse for the reader than one product fewer.
    """
    text = _AMAZON_PREFIX_RE.sub("", title.strip()).lstrip(" :-|")
    head = _TITLE_CUT_RE.split(text, maxsplit=1)[0].strip().rstrip(". ")
    candidate = (head or text)[:90].strip()
    if len(candidate) < _MIN_DISPLAY_CHARS or candidate.lower() in _GENERIC_DISPLAYS:
        return ""
    return candidate


def _key_for(display: str, asin: str) -> str:
    """A catalog key matching `lib.affiliate_resolver`'s placeholder grammar
    (`[a-z0-9][a-z0-9_-]*`). The ASIN suffix keeps two similarly-named
    listings from colliding into one key."""
    slug = _KEY_STRIP_RE.sub("-", display.lower()).strip("-")[:40].strip("-")
    return f"{slug or 'product'}-{asin.lower()}"


def _entries_from_results(results: list[dict[str, Any]]) -> dict[str, ProductEntry]:
    """ProductEntries for every result whose URL carries an ASIN."""
    found: dict[str, ProductEntry] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        match = _ASIN_RE.search(str(item.get("link") or ""))
        title = str(item.get("title") or "").strip()
        if match is None or not title:
            continue
        asin = match.group(1)
        display = _clean_display(title)
        if not display:
            logger.info("crew_products_discovery_dropped_generic_title", asin=asin, title=title)
            continue
        key = _key_for(display, asin)
        if key in found:
            continue
        found[key] = ProductEntry(
            key=key,
            asin=asin,
            display=display,
            category=None,
            # The SERP snippet is the only "why this product" context a
            # discovered entry has; the selector reasons over this field the
            # same way it reads a curated entry's hand-written notes.
            notes=str(item.get("snippet") or "").strip()[:280] or None,
        )
    return found


def _read_cache(brand_dir: Path) -> dict[str, ProductEntry]:
    """Cached discoveries still inside the TTL. `{}` on any read problem."""
    path = brand_dir / _CACHE_RELATIVE
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("crew_products_discovery_cache_unreadable", error=str(exc))
        return {}
    cutoff = (datetime.now(UTC) - timedelta(days=_CACHE_TTL_DAYS)).isoformat()
    out: dict[str, ProductEntry] = {}
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or str(entry.get("ts") or "") < cutoff:
            continue
        try:
            out[entry["key"]] = ProductEntry(
                key=entry["key"],
                asin=entry["asin"],
                display=entry["display"],
                category=entry.get("category"),
                notes=entry.get("notes"),
            )
        except KeyError:
            continue
    return out


def _write_cache(brand_dir: Path, entries: dict[str, ProductEntry]) -> None:
    """Merge `entries` into the cache. Best-effort: a write failure is logged
    and discovery still returns its results for this run."""
    path = brand_dir / _CACHE_RELATIVE
    now = datetime.now(UTC).isoformat()
    merged = {
        key: {
            "key": entry.key,
            "asin": entry.asin,
            "display": entry.display,
            "category": entry.category,
            "notes": entry.notes,
            "ts": now,
        }
        for key, entry in {**_read_cache(brand_dir), **entries}.items()
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(list(merged.values()), indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("crew_products_discovery_cache_write_failed", error=str(exc))


def discover_products(
    brand_dir: Path,
    queries: list[str],
    *,
    search_fn: SearchFn | None = None,
    max_products: int = _MAX_DISCOVERED,
) -> dict[str, ProductEntry]:
    """Amazon products for `queries`, as catalog entries the selector can pick.

    `{}` when there are no queries, no API key, or every search fails --
    callers treat that as "nothing discovered", never an error. Results are
    cached under `data/cache/discovered_products.json` so a re-draft of the
    same post costs no further searches.
    """
    if not queries:
        return {}

    search = search_fn or _serper_search
    discovered: dict[str, ProductEntry] = {}
    for query in queries:
        for key, entry in _entries_from_results(search(query, _RESULTS_PER_QUERY)).items():
            discovered.setdefault(key, entry)
        if len(discovered) >= max_products:
            break

    limited = dict(list(discovered.items())[:max_products])
    logger.info(
        "crew_products_discovered",
        query_count=len(queries),
        product_count=len(limited),
        asins=[e.asin for e in limited.values()],
    )
    if limited:
        _write_cache(brand_dir, limited)
    return limited
