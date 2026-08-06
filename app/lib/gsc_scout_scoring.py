"""Pure scoring logic for the GSC opportunity scout (`lib.gsc_scout`).

Dependency-free (no `lib.db`, no `lib.ideas_db`, no network) so the scoring
rules are unit-testable against plain dicts/dataclasses without mocking
anything. `lib.gsc_scout` wires this to the real `published_content_db`
repository and `ideas_db.insert_idea`.

Anti-cannibalization + opportunity classification, per the plan's Slice 2
description ("scores opportunities: query position/impressions +
cannibalization check against `published_content` and
`site_content_cache.json`"):

  - A keyword already covered by a recent post's title/tags is skipped --
    proposing it again would duplicate existing content.
  - A keyword where an owned URL already ranks at position <= 5 is skipped
    -- it's already winning; nothing to gain from a competing idea.
  - A keyword where 2+ distinct owned URLs already show for overlapping
    GSC queries is skipped -- that is itself the cannibalization signal
    Slice 2.5 flags for merge/redirect; adding a third page would make it
    worse, not better.
  - Everything else is scored: "optimize" (real GSC position in the
    winnable 6-25 band with real impressions) > "emerging" (some GSC
    signal, not yet in the winnable band) > "discovery" (no GSC signal at
    all -- a pure topical gap).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

WINNABLE_MIN_POSITION = 6.0
WINNABLE_MAX_POSITION = 25.0
WINNABLE_MIN_IMPRESSIONS = 40

ALREADY_WINNING_MAX_POSITION = 5.0
CANNIBALIZATION_MIN_URLS = 2

_SCORE_BASE: dict[str, float] = {"optimize": 70.0, "emerging": 50.0, "discovery": 30.0}
_INSUFFICIENT_DATA_PENALTY = 0.7


@dataclass(frozen=True)
class KeywordSeed:
    """One candidate topic pulled from onboarded keyword sources."""

    keyword: str
    category: str = "General"
    source: str = "content_analysis"  # content_analysis | cluster_pillar | cluster_spoke
    priority: int = 5  # lower = more urgent (cluster priority; mid default otherwise)
    notes: str = ""


@dataclass(frozen=True)
class GscOpportunity:
    """A scored, ready-to-insert content idea."""

    keyword: str
    category: str
    opportunity_type: str  # optimize | emerging | discovery
    score: float
    matched_query: str
    best_position: float | None
    impressions: int
    cannibalization_urls: tuple[str, ...]
    reason: str


def _matches(keyword: str, query: str) -> bool:
    """Loose substring match, either direction, case-insensitive."""
    k, q = keyword.lower().strip(), query.lower().strip()
    return bool(k) and bool(q) and (k in q or q in k)


def _matching_rows(keyword: str, published_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in published_rows if _matches(keyword, str(row.get("gsc_query", "")))]


def _distinct_urls(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(sorted({str(r["wp_url"]) for r in rows if r.get("wp_url")}))


def _already_covered(keyword: str, site_posts: list[dict[str, Any]]) -> bool:
    """True if a recent post's title/tags already substantially covers this keyword."""
    k = keyword.lower()
    for post in site_posts:
        title = str(post.get("title", "")).lower()
        tags = [str(t).lower() for t in post.get("tags", [])]
        if k in title or any(_matches(k, tag) for tag in tags):
            return True
    return False


def score_keyword_seed(
    seed: KeywordSeed,
    published_rows: list[dict[str, Any]],
    site_posts: list[dict[str, Any]],
    *,
    data_sufficient: bool,
) -> GscOpportunity | None:
    """Score one keyword seed, or `None` if it should be skipped (see module docstring)."""
    if _already_covered(seed.keyword, site_posts):
        return None

    matches = _matching_rows(seed.keyword, published_rows)
    urls = _distinct_urls(matches)
    if len(urls) >= CANNIBALIZATION_MIN_URLS:
        return None
    if (
        matches
        and min(float(r.get("position", 999)) for r in matches) <= ALREADY_WINNING_MAX_POSITION
    ):
        return None

    best_position: float | None
    if matches:
        best = min(matches, key=lambda r: float(r.get("position", 999)))
        best_position = float(best.get("position", 0.0))
        impressions = sum(int(r.get("impressions", 0)) for r in matches)
        matched_query = str(best.get("gsc_query", ""))
        winnable = (
            WINNABLE_MIN_POSITION <= best_position <= WINNABLE_MAX_POSITION
            and impressions >= WINNABLE_MIN_IMPRESSIONS
        )
        opportunity_type = "optimize" if winnable else "emerging"
    else:
        best_position, impressions, matched_query = None, 0, ""
        opportunity_type = "discovery"

    score = _SCORE_BASE[opportunity_type]
    score += max(0.0, 10.0 - min(seed.priority, 10)) * 2.0  # cluster-priority bonus
    score += min(impressions / 10.0, 20.0)  # impressions bonus, capped

    reason = f"{opportunity_type} opportunity for '{seed.keyword}'"
    if matched_query:
        reason += (
            f" (best GSC query '{matched_query}', position {best_position:.1f}, "
            f"impressions {impressions})"
        )
    if not data_sufficient:
        score *= _INSUFFICIENT_DATA_PENALTY
        opportunity_type = "discovery"
        reason += " -- insufficient GSC history: treat as discovery, not optimization"

    return GscOpportunity(
        keyword=seed.keyword,
        category=seed.category,
        opportunity_type=opportunity_type,
        score=round(score, 2),
        matched_query=matched_query,
        best_position=best_position,
        impressions=impressions,
        cannibalization_urls=urls,
        reason=reason,
    )


def rank_opportunities(
    seeds: list[KeywordSeed],
    published_rows: list[dict[str, Any]],
    site_posts: list[dict[str, Any]],
    *,
    data_sufficient: bool,
    top_n: int = 10,
) -> list[GscOpportunity]:
    """Score every seed, drop skips/duplicates, return the top-N by score."""
    seen: set[str] = set()
    scored: list[GscOpportunity] = []
    for seed in seeds:
        key = seed.keyword.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        opportunity = score_keyword_seed(
            seed, published_rows, site_posts, data_sufficient=data_sufficient
        )
        if opportunity is not None:
            scored.append(opportunity)
    scored.sort(key=lambda o: o.score, reverse=True)
    return scored[:top_n]
