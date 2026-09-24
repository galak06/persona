"""Brand-driven relevance rules for fb-group-scout candidates.

The engine only knows *kinds* of groups that never yield a comment for any
brand (marketplaces, travel, medical support, business promotion) — the FB
engager's drafter refuses those posts, so joining them buys zero engagement.
Everything brand-specific comes from the brand dir:

``<BRAND_DIR>/brand.json`` top-level ``group_scout`` block (hand-owned; brand
provisioning shallow-merges brand.json and only rewrites ``runtime`` /
``group_discovery``, so a key there would be wiped on every settings save)::

    "group_scout": {
      "search_queries": ["homemade dog food", ...],
      "exclude_terms": ["..."],          # added to the engine defaults
      "target_countries": ["US", "CA"]   # overrides brand.target_market
    }

Fallbacks: queries <- ``config.json`` ``content_analysis.keywords`` primary
list; countries <- ``brand.target_market`` parsed by ``geo.parse_countries``;
neither present -> no queries / no geo restriction.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.group_discovery.geo import (
    GAZETTEER,
    contains_phrase,
    detect_countries,
    normalize,
    parse_countries,
)

# Group kinds the engager can never comment in. Matched as whole words/phrases
# after punctuation is collapsed, so "Buy/Sell/Trade" reads as "buy sell trade".
DEFAULT_EXCLUDE_TERMS: dict[str, tuple[str, ...]] = {
    "marketplace": (
        "buy sell",
        "buy and sell",
        "buy n sell",
        "b s t",
        "bst",
        "for sale",
        "marketplace",
        "rehoming",
        "rehome",
        "puppies for sale",
        "selling",
        "wts",
        "wtb",
        "swap",
    ),
    "travel": (
        "hotel",
        "hotels",
        "holiday",
        "holidays",
        "vacation",
        "vacations",
        "cottage",
        "cottages",
        "resort",
        "resorts",
        "travel",
        "travelling",
        "traveling",
        "airbnb",
        "lodging",
        "accommodation",
        "accommodations",
    ),
    "medical": (
        "stroke",
        "spinal",
        "fce",
        "ivdd",
        "paralysis",
        "paralyzed",
        "bladder",
        "incontinence",
        "kidney disease",
        "cancer",
        "tumor",
        "epilepsy",
        "seizure",
        "seizures",
        "diabetes",
        "diabetic",
        "cushing's",
        "cushings",
        "addison's",
        "megaesophagus",
        "heart disease",
        "pancreatitis",
        "rainbow bridge",
        "pet loss",
        "grief",
    ),
    "promotion": (
        "business",
        "businesses",
        "recommendations",
        "in search of",
        "iso",
        "promote",
        "promotion",
        "promotions",
        "advertise",
        "advertising",
        "marketing",
        "self promo",
        "small business",
    ),
}
MAX_DERIVED_QUERIES = 12


@dataclass(frozen=True)
class ScoutRules:
    """What one brand's scout searches for and refuses to join."""

    search_queries: tuple[str, ...] = ()
    exclude_terms: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_EXCLUDE_TERMS)
    )
    target_countries: frozenset[str] = frozenset()


def _group_text(g: dict[str, Any]) -> str:
    return f"{g.get('name') or ''} {g.get('description') or ''}"


def off_target_reason(g: dict[str, Any], rules: ScoutRules) -> str | None:
    """Why ``g`` must never be joined, or None when it is on target."""
    norm = normalize(_group_text(g))
    for category, terms in rules.exclude_terms.items():
        hit = next((t for t in terms if contains_phrase(norm, t)), None)
        if hit:
            return f"{category}: {hit!r}"
    if rules.target_countries:
        foreign = detect_countries(_group_text(g), ignore=rules.target_countries)
        if foreign:
            return f"geo: {', '.join(sorted(foreign))} outside target market"
    return None


def mentions_target_country(g: dict[str, Any], rules: ScoutRules) -> bool:
    """True when the group text names one of the brand's target countries."""
    norm = normalize(_group_text(g))
    return any(
        contains_phrase(norm, alias)
        for code in rules.target_countries
        for alias in GAZETTEER.get(code, ())
    )


def drop_off_target(groups: list[dict[str, Any]], rules: ScoutRules) -> list[dict[str, Any]]:
    """Filter out off-target groups (e.g. queued by an older, looser scout)."""
    kept: list[dict[str, Any]] = []
    for g in groups:
        reason = off_target_reason(g, rules)
        if reason:
            print(f"  [off-target] {g.get('name', '?')}: {reason} — dropped")
        else:
            kept.append(g)
    return kept


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def _derived_queries(config: dict[str, Any]) -> list[str]:
    analysis = config.get("content_analysis")
    keywords = analysis.get("keywords") if isinstance(analysis, dict) else None
    if not isinstance(keywords, dict):
        return []
    primary = _str_list(keywords.get("primary_keywords") or keywords.get("primary"))
    return primary[:MAX_DERIVED_QUERIES]


def load_scout_rules(brand_dir: Path | None = None) -> ScoutRules:
    """Build ``ScoutRules`` from the brand dir (``BRAND_DIR`` when omitted)."""
    if brand_dir is None:
        env_dir = os.environ.get("BRAND_DIR")
        if not env_dir:
            return ScoutRules()
        brand_dir = Path(env_dir)
    brand = _read_json(brand_dir / "brand.json")
    block = brand.get("group_scout")
    scout: dict[str, Any] = block if isinstance(block, dict) else {}

    queries = _str_list(scout.get("search_queries")) or _derived_queries(
        _read_json(brand_dir / "config.json")
    )
    terms = dict(DEFAULT_EXCLUDE_TERMS)
    extra = _str_list(scout.get("exclude_terms"))
    if extra:
        terms["brand"] = tuple(extra)

    countries = frozenset(c.upper() for c in _str_list(scout.get("target_countries")))
    if not countries:
        identity = brand.get("brand")
        market = identity.get("target_market") if isinstance(identity, dict) else None
        countries = parse_countries(market) if isinstance(market, str) else frozenset()

    return ScoutRules(
        search_queries=tuple(dict.fromkeys(queries)),
        exclude_terms=terms,
        target_countries=countries,
    )
