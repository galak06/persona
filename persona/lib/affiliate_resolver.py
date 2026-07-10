"""Resolve [AFFILIATE:product_key] placeholders to Amazon affiliate URLs.

`wp-post-creator` drops placeholders like `[AFFILIATE:nom-nom-fresh]` into
draft HTML. This module is the final step before WP publish: it swaps each
placeholder for a real Amazon URL (with our associate tag appended) looked
up from `data/affiliate_products.json`.

Two hard guards before we resolve anything:

  1. `AMAZON_ASSOCIATES_TAG` must be set — without it we'd publish naked
     Amazon links, which violates Associates T&C (unattributed traffic
     earns nothing and can get the account flagged).
  2. The HTML must include the required disclosure block — the FTC/Amazon
     disclosure has to appear at least once on every post that contains
     affiliate links.

If either guard trips, we raise `AffiliateResolverError` and leave the
HTML untouched.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CATALOG_FILE = _PROJECT_ROOT / "data" / "affiliate_products.json"

_PLACEHOLDER_RE = re.compile(r"\[AFFILIATE:([a-z0-9][a-z0-9_-]*)\]", re.IGNORECASE)

# Disclosure tokens that satisfy FTC / Amazon requirements. Any one of these
# substrings present in the post body is enough.
_DISCLOSURE_MARKERS = (
    "affiliate disclosure",
    "amazon affiliate",
    "as an amazon associate",
    "affiliate link",
)


class AffiliateResolverError(RuntimeError):
    """Raised when a placeholder can't be resolved safely."""


@dataclass(frozen=True)
class ProductEntry:
    key: str
    asin: str
    display: str
    category: str | None = None
    notes: str | None = None


def _load_catalog() -> dict[str, ProductEntry]:
    if not _CATALOG_FILE.exists():
        raise AffiliateResolverError(
            f"catalog missing: {_CATALOG_FILE} — "
            "create it with an array of {key, asin, display} entries"
        )
    raw = json.loads(_CATALOG_FILE.read_text())
    out: dict[str, ProductEntry] = {}
    for entry in raw:
        key = entry["key"].lower()
        if key in out:
            raise AffiliateResolverError(f"duplicate product key in catalog: {key!r}")
        out[key] = ProductEntry(
            key=key,
            asin=entry["asin"],
            display=entry.get("display", key),
            category=entry.get("category"),
            notes=entry.get("notes"),
        )
    return out


def build_affiliate_url(asin: str, tag: str, campaign_id: str | None = None) -> str:
    """Return the canonical Amazon.com product URL with our associate tag.

    If `campaign_id` is given it goes in as the `ascsubtag` — Amazon reports
    aggregate that field, so we can attribute clicks to a specific campaign.
    """
    url = f"https://www.amazon.com/dp/{asin}?tag={tag}"
    if campaign_id:
        url += f"&ascsubtag={campaign_id}"
    return url


def resolve_html(
    html: str,
    *,
    associates_tag: str | None = None,
    campaign_id: str | None = None,
    catalog: dict[str, ProductEntry] | None = None,
) -> str:
    """Replace every [AFFILIATE:key] in `html` with a real Amazon URL.

    Raises `AffiliateResolverError` if the associates tag is missing, the
    disclosure block is missing, or any placeholder key is unknown.
    """
    tag = associates_tag or os.environ.get("AMAZON_ASSOCIATES_TAG", "").strip()
    if not tag:
        raise AffiliateResolverError(
            "AMAZON_ASSOCIATES_TAG not set — add it to .claude/settings.local.json env"
        )
    if not _has_disclosure(html):
        raise AffiliateResolverError(
            "post body is missing an affiliate disclosure block — "
            "refusing to inject links without it (FTC/Amazon requirement)"
        )

    catalog = catalog if catalog is not None else _load_catalog()

    matches = list(_PLACEHOLDER_RE.finditer(html))
    if not matches:
        return html  # no placeholders, nothing to do

    unknown = {m.group(1).lower() for m in matches} - set(catalog.keys())
    if unknown:
        raise AffiliateResolverError(
            f"unknown product key(s) in catalog: {sorted(unknown)} — "
            f"add them to {_CATALOG_FILE.name}"
        )

    def _sub(match: re.Match[str]) -> str:
        entry = catalog[match.group(1).lower()]
        return build_affiliate_url(entry.asin, tag, campaign_id)

    out = _PLACEHOLDER_RE.sub(_sub, html)
    logger.info(
        "affiliate_resolver: replaced %d placeholder(s), campaign=%s",
        len(matches),
        campaign_id or "-",
    )
    return out


def _has_disclosure(html: str) -> bool:
    lower = html.lower()
    return any(marker in lower for marker in _DISCLOSURE_MARKERS)


def lookup(key: str, catalog: dict[str, ProductEntry] | None = None) -> ProductEntry:
    """Look up a product by key. Raises if not found."""
    catalog = catalog if catalog is not None else _load_catalog()
    try:
        return catalog[key.lower()]
    except KeyError as exc:
        raise AffiliateResolverError(f"unknown product key: {key!r}") from exc
