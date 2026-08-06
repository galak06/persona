"""Orchestrates the CrewAI content-opportunity scout for one brand.

Combines two signal sources into one final, deduped, ranked list of content
ideas, then writes winners to `content_ideas` (`lib.ideas_db.insert_idea`),
same table and row shape `lib.gsc_scout.run_scout` already writes to (see
`_idea_row` below) so downstream consumers (`content-enricher`,
`wp-post-creator`, `performance-tracker`) see one consistent shape regardless
of which scout produced a row. Tagged `"source": "crewai_scout"` in `input`
so the two are distinguishable later.

  1. `lib.gsc_scout_scoring.rank_opportunities` -- real GSC ranking data,
     already cannibalization-checked (read via the same building blocks
     `lib.gsc_scout.run_scout` uses; this module never calls `run_scout`
     itself, so the two scouts never double-insert the same GSC opportunity).
  2. Live web search (Serper, via the agent's `SerperDevTool`) for topics not
     already in the brand's keyword seed list.

Every external call (the CrewAI `kickoff()`, `insert_idea`) is wrapped so a
failure is logged and this never raises out to the caller -- matching
`lib.ideas_db`/`lib.gsc_scout`'s "never crash the run" convention.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from crewai import Agent, Task

from lib import ideas_db
from lib.crew.agent import build_scout_agent, build_scout_task
from lib.crew.context import (
    brand_identity_summary,
    brand_voice_summary,
    build_task_description,
    seed_keywords_summary,
    serialize_opportunities,
)
from lib.crew.models import IdeaCandidate, ScoutOutput
from lib.gsc_data_sufficiency import evaluate_data_sufficiency
from lib.gsc_scout import load_keyword_seeds, load_site_content_cache
from lib.gsc_scout_scoring import rank_opportunities
from lib.observability import get_logger
from lib.published_content_db.repository import PublishedContentRepository

logger = get_logger(__name__)

ExecuteFn = Callable[[Agent, Task], ScoutOutput | None]

_DISCOVERY_TYPES = frozenset({"discovery", "web_discovery"})


def _read_config(brand_dir: Path) -> dict[str, Any]:
    """`config.json`, tolerant of a missing/malformed file (never raises) --
    a small local copy of `lib.gsc_scout`'s private `_read_json` rather than
    importing that module's underscore-prefixed helper directly."""
    path = brand_dir / "config.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("crew_scout_config_json_parse_failed", path=str(path))
        return {}
    return data if isinstance(data, dict) else {}


def execute_scout_crew(agent: Agent, task: Task) -> ScoutOutput | None:
    """Run the real `Crew(agents=[agent], tasks=[task]).kickoff()` and return
    the structured `ScoutOutput`, or `None` on any failure (logged, not raised).

    This is the only function in this module that makes a real LLM/network
    call; `run_crew_scout` accepts an `execute_fn` override so tests never
    exercise this path.
    """
    from crewai import Crew  # local import: keeps Crew construction next to its one use

    try:
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        crew.kickoff()
    except Exception as exc:  # CrewAI/LiteLLM/network errors -- never crash the run
        logger.warning("crew_scout_kickoff_failed", error=str(exc))
        return None

    output = task.output
    if output is None or output.pydantic is None:
        logger.warning("crew_scout_kickoff_no_structured_output")
        return None
    result = output.pydantic
    if not isinstance(result, ScoutOutput):
        logger.warning("crew_scout_kickoff_unexpected_output_type", type=type(result).__name__)
        return None
    return result


def _idea_row(idea: IdeaCandidate, *, data_sufficient: bool) -> dict[str, Any]:
    return {
        "category": idea.category,
        "topic": idea.topic.strip(),
        "target_keyword": idea.target_keyword,
        # Same real key `ideas_db.insert_idea` reads as `lib.gsc_scout._idea_row`
        # uses (see that module's note on the `nalla_context`/`persona_context`
        # drift) -- kept identical here so both scouts produce one row shape.
        "nalla_context": idea.reasoning,
        "post_goal": "topic_discovery"
        if idea.opportunity_type in _DISCOVERY_TYPES
        else "seo_traffic",
        "status": "publish",
        "input": json.dumps(
            {
                "source": "crewai_scout",
                "opportunity_type": idea.opportunity_type,
                "priority_score": idea.priority_score,
                "reasoning": idea.reasoning,
                "data_sufficient": data_sufficient,
            }
        ),
    }


def run_crew_scout(
    brand_dir: Path,
    *,
    repo: PublishedContentRepository | None = None,
    insert_idea_fn: Callable[..., str | None] | None = None,
    existing_topics_fn: Callable[..., set[str]] | None = None,
    execute_fn: ExecuteFn | None = None,
    top_n: int = 10,
    dry_run: bool = False,
) -> list[tuple[IdeaCandidate, str | None]]:
    """Score + discover content opportunities for one brand and write them to
    `content_ideas`.

    Returns `(idea, idea_id)` pairs; `idea_id` is `None` for a duplicate
    topic, a dry run, or a failed insert. Returns `[]` (logged, not raised)
    if the CrewAI kickoff itself fails or returns no structured output.
    """
    brand_id = brand_dir.name
    config = _read_config(brand_dir)
    brand_name = str(config.get("site", {}).get("name") or brand_id)

    repo = repo or PublishedContentRepository()
    insert_idea_fn = insert_idea_fn or ideas_db.insert_idea
    existing_topics_fn = existing_topics_fn or ideas_db.existing_topics
    execute_fn = execute_fn or execute_scout_crew

    seeds = load_keyword_seeds(brand_dir)
    site_posts = list(load_site_content_cache(brand_dir).get("recent_posts", []) or [])
    published_rows = repo.list_for_brand(brand_id, limit=10_000)
    sufficiency = evaluate_data_sufficiency(
        [(str(r["gsc_query"]), float(r["position"]), int(r["impressions"])) for r in published_rows]
    )
    opportunities = rank_opportunities(
        seeds, published_rows, site_posts, data_sufficient=sufficiency.sufficient, top_n=top_n
    )

    description = build_task_description(
        identity=brand_identity_summary(config),
        voice=brand_voice_summary(brand_dir),
        seed_keywords=seed_keywords_summary(seeds),
        opportunities_json=serialize_opportunities(opportunities),
        data_sufficient=sufficiency.sufficient,
        top_n=top_n,
    )

    agent = build_scout_agent()
    task = build_scout_task(agent, description)
    try:
        output = execute_fn(agent, task)
    except Exception as exc:  # defense in depth: execute_scout_crew (the real
        # default) already catches internally, but this also protects against
        # any custom `execute_fn` a caller injects that doesn't -- same "never
        # crash the run" contract as `lib.gsc_scout.run_scout`.
        logger.warning("crew_scout_execute_fn_raised", brand_id=brand_id, error=str(exc))
        return []
    if output is None:
        logger.warning("crew_scout_run_no_output", brand_id=brand_id)
        return []

    existing_topics = existing_topics_fn(brand_id=brand_id)
    results: list[tuple[IdeaCandidate, str | None]] = []
    for idea in output.ideas:
        if idea.topic.strip().lower() in existing_topics:
            logger.info("crew_scout_skip_duplicate_topic", brand_id=brand_id, topic=idea.topic)
            results.append((idea, None))
            continue
        if dry_run:
            logger.info(
                "crew_scout_dry_run_would_insert",
                brand_id=brand_id,
                topic=idea.topic,
                opportunity_type=idea.opportunity_type,
                priority_score=idea.priority_score,
            )
            results.append((idea, None))
            continue
        idea_id = insert_idea_fn(
            _idea_row(idea, data_sufficient=sufficiency.sufficient),
            brand_id=brand_id,
            brand_name=brand_name,
        )
        results.append((idea, idea_id))

    logger.info(
        "crew_scout_run_complete",
        brand_id=brand_id,
        candidates=len(output.ideas),
        inserted=sum(1 for _, idea_id in results if idea_id),
        dry_run=dry_run,
        data_sufficient=sufficiency.sufficient,
    )
    return results
