"""Read-only view of the scout's search vocabulary.

  GET /api/v1/keywords -- curated + discovered terms for the active brand.

The scout searches two vocabularies merged together: the operator's curated
`content_analysis.keywords` in `config.json`, and the terms
`lib.crew.trends` discovered on previous runs (`lib.crew.keyword_store`).
Only the discovered half changes by itself, and until now it changed with no
way to see it -- the terms lived in a JSON file under `state/` that nothing
surfaced. This endpoint exists so "why did the scout suggest that?" has an
answer that does not involve reading a file inside a container.

`active` is the field worth understanding. The store is additive and grows
forever, but only the highest-scoring `active_limit` discovered terms are
merged into a run's vocabulary, so a term can be on file and still not be
influencing anything. Rendering the inactive ones too (rather than only what
`active_seeds` returns) is deliberate: a term sitting just below the cut is
exactly what an operator needs to see when deciding whether the limit is
right.

Read-only on purpose. Pruning a bad term means deleting from an
additive-by-design store, which is a different decision with different
safety requirements -- not something to bolt onto a viewer.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from api.brand_context import resolve_api_brand
from lib.crew import keyword_store
from lib.gsc_scout import load_keyword_seeds

router = APIRouter()


class CuratedKeyword(BaseModel):
    """One term from the brand's own `content_analysis.keywords` block."""

    keyword: str
    category: str


class DiscoveredKeyword(BaseModel):
    """One term the Trends stage found, with the provenance behind it."""

    keyword: str
    category: str
    first_seen: str
    last_seen: str
    times_seen: int
    best_score: float
    reason: str
    # False = on file but below the active cut, so not in the search vocabulary.
    active: bool


class KeywordsResponse(BaseModel):
    curated: list[CuratedKeyword]
    discovered: list[DiscoveredKeyword]
    active_limit: int


@router.get("/keywords", response_model=KeywordsResponse, summary="Scout search vocabulary")
def get_keywords() -> KeywordsResponse:
    """Curated and discovered keywords for the active brand, newest-strongest
    first. Never 404s: a brand with no discoveries yet returns an empty
    `discovered` list rather than an error, because that is the normal state
    before the first scout run."""
    _brand_id, brand_dir_str = resolve_api_brand()
    brand_dir = Path(brand_dir_str)

    curated_seeds = load_keyword_seeds(brand_dir)
    curated_terms = {s.keyword.strip().lower() for s in curated_seeds}

    # The same call the scout itself makes, so what's marked active here is
    # exactly what a run would use -- not a re-derivation that could drift.
    active_terms = {
        s.keyword.strip().lower()
        for s in keyword_store.active_seeds(brand_dir, exclude=curated_terms)
    }

    discovered = [
        DiscoveredKeyword(
            keyword=str(entry.get("keyword") or key),
            category=str(entry.get("category") or "General"),
            first_seen=str(entry.get("first_seen") or ""),
            last_seen=str(entry.get("last_seen") or ""),
            times_seen=int(entry.get("times_seen") or 0),
            best_score=float(entry.get("best_score") or 0.0),
            reason=str(entry.get("reason") or ""),
            active=key in active_terms,
        )
        for key, entry in keyword_store.load(brand_dir).items()
        if isinstance(entry, dict)
    ]
    # Active first, then by the score that decides the cut -- so the boundary
    # between "in the vocabulary" and "on file only" is visible at a glance.
    discovered.sort(key=lambda k: (not k.active, -k.best_score, k.keyword))

    return KeywordsResponse(
        curated=[
            CuratedKeyword(keyword=s.keyword, category=s.category)
            for s in sorted(curated_seeds, key=lambda s: (s.category, s.keyword))
        ],
        discovered=discovered,
        active_limit=keyword_store.DEFAULT_ACTIVE_LIMIT,
    )
