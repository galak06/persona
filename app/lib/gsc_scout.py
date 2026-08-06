"""GSC opportunity scout -- reads onboarded keyword seeds + `published_content`
+ `site_content_cache.json`, scores content opportunities (`lib.gsc_scout_scoring`),
and writes the winners into the *existing* Supabase `content_ideas` table via
`lib.ideas_db.insert_idea` -- the same store `content-ideator` already
reads/writes, so a scout run is immediately consumable by
`content-enricher`/`wp-post-creator`/`performance-tracker` without any other
new plumbing.

Invoked from `scripts/backfill_gsc_content.py` after a backfill (the natural
end-to-end sequence the plan describes for Slice 2); callable directly by
anything else that already has a brand directory.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lib import ideas_db
from lib.gsc_data_sufficiency import evaluate_data_sufficiency
from lib.gsc_scout_scoring import GscOpportunity, KeywordSeed, rank_opportunities
from lib.observability import get_logger
from lib.published_content_db.repository import PublishedContentRepository

logger = get_logger(__name__)

# Brand data moved under a `data/cache/` subfolder in the 2026-06 reorg (see
# project memory); several older readers still point at the pre-reorg path
# and haven't been migrated. Current location is tried first.
_CURRENT_SITE_CACHE = Path("data") / "cache" / "site_content_cache.json"
_LEGACY_SITE_CACHE = Path("data") / "site_content_cache.json"

_COMPETITOR_CATEGORY_HINT = "competitor"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("gsc_scout_json_parse_failed", path=str(path))
        return {}
    return data if isinstance(data, dict) else {}


def load_site_content_cache(brand_dir: Path) -> dict[str, Any]:
    """Read `site-analyzer`'s cache: current path first, legacy path as fallback."""
    for rel in (_CURRENT_SITE_CACHE, _LEGACY_SITE_CACHE):
        candidate = brand_dir / rel
        if candidate.exists():
            return _read_json(candidate)
    return {}


def _content_analysis_seeds(config: dict[str, Any]) -> list[KeywordSeed]:
    """Flatten `content_analysis.keywords` (all categories except competitor ones --
    those are engagement-scan seeds, not candidate topics for our own content)."""
    keywords_block = config.get("content_analysis", {}).get("keywords", {})
    seeds: list[KeywordSeed] = []
    for category, terms in keywords_block.items():
        if _COMPETITOR_CATEGORY_HINT in str(category).lower() or not isinstance(terms, list):
            continue
        for term in terms:
            text = str(term).strip()
            if text:
                seeds.append(KeywordSeed(keyword=text, category=f"content_analysis:{category}"))
    return seeds


def load_keyword_seeds(brand_dir: Path) -> list[KeywordSeed]:
    """All candidate topics from the brand's own onboarded config -- no longer
    reads `keyword_clusters.json` (a static, manually-regenerated file with no
    freshness guarantee; retired repo-wide in favor of brand-grounded sources:
    `content_analysis.keywords` here, plus real GSC signal and live web search
    layered on top by the CrewAI scout)."""
    config = _read_json(brand_dir / "config.json")
    return _content_analysis_seeds(config)


def _idea_row(opportunity: GscOpportunity, *, data_sufficient: bool) -> dict[str, Any]:
    return {
        "category": opportunity.category,
        "topic": opportunity.keyword.strip().capitalize(),
        "target_keyword": opportunity.keyword,
        # `ideas_db.insert_idea()` reads the dict key `nalla_context` (confirmed
        # against lib/ideas_db.py and recipe-publisher/workers/worker_content_ideator.py's
        # existing call site) -- the plan's Slice 2 prose names `persona_context`
        # (matching create_supabase_schema.sql's column), but that is not what the
        # live insert path actually writes. This follows the real code.
        "nalla_context": opportunity.reason,
        "post_goal": "seo_traffic"
        if opportunity.opportunity_type != "discovery"
        else "topic_discovery",
        "status": "publish",
        "input": json.dumps(
            {
                "source": "gsc_scout",
                "opportunity_type": opportunity.opportunity_type,
                "matched_query": opportunity.matched_query,
                "best_position": opportunity.best_position,
                "impressions": opportunity.impressions,
                "score": opportunity.score,
                "cannibalization_urls": list(opportunity.cannibalization_urls),
                "data_sufficient": data_sufficient,
            }
        ),
    }


def run_scout(
    brand_dir: Path,
    *,
    repo: PublishedContentRepository | None = None,
    insert_idea_fn: Callable[..., str | None] | None = None,
    existing_topics_fn: Callable[..., set[str]] | None = None,
    top_n: int = 10,
    dry_run: bool = False,
) -> list[tuple[GscOpportunity, str | None]]:
    """Score GSC opportunities for one brand and write them to `content_ideas`.

    Returns `(opportunity, idea_id)` pairs; `idea_id` is `None` for a
    duplicate topic, a dry run, or a failed insert (`insert_idea` itself
    never raises -- see `lib.ideas_db`).
    """
    brand_id = brand_dir.name
    config = _read_json(brand_dir / "config.json")
    brand_name = str(config.get("site", {}).get("name") or brand_id)

    repo = repo or PublishedContentRepository()
    insert_idea_fn = insert_idea_fn or ideas_db.insert_idea
    existing_topics_fn = existing_topics_fn or ideas_db.existing_topics

    published_rows = repo.list_for_brand(brand_id, limit=10_000)
    sufficiency = evaluate_data_sufficiency(
        [(str(r["gsc_query"]), float(r["position"]), int(r["impressions"])) for r in published_rows]
    )
    if not sufficiency.sufficient:
        logger.warning(
            "gsc_scout_insufficient_data",
            brand_id=brand_id,
            qualifying_query_count=sufficiency.qualifying_query_count,
            threshold=sufficiency.threshold,
            note="insufficient GSC history -- treating scout output as discovery, not optimization",
        )

    seeds = load_keyword_seeds(brand_dir)
    site_posts = list(load_site_content_cache(brand_dir).get("recent_posts", []) or [])
    opportunities = rank_opportunities(
        seeds, published_rows, site_posts, data_sufficient=sufficiency.sufficient, top_n=top_n
    )

    existing_topics = existing_topics_fn(brand_id=brand_id)
    results: list[tuple[GscOpportunity, str | None]] = []
    for opportunity in opportunities:
        if opportunity.keyword.strip().lower() in existing_topics:
            logger.info(
                "gsc_scout_skip_duplicate_topic", brand_id=brand_id, keyword=opportunity.keyword
            )
            results.append((opportunity, None))
            continue
        if dry_run:
            logger.info(
                "gsc_scout_dry_run_would_insert",
                brand_id=brand_id,
                keyword=opportunity.keyword,
                score=opportunity.score,
                opportunity_type=opportunity.opportunity_type,
            )
            results.append((opportunity, None))
            continue
        idea_id = insert_idea_fn(
            _idea_row(opportunity, data_sufficient=sufficiency.sufficient),
            brand_id=brand_id,
            brand_name=brand_name,
        )
        results.append((opportunity, idea_id))

    logger.info(
        "gsc_scout_run_complete",
        brand_id=brand_id,
        candidates=len(opportunities),
        inserted=sum(1 for _, idea_id in results if idea_id),
        dry_run=dry_run,
        data_sufficient=sufficiency.sufficient,
    )
    return results
