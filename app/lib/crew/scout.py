"""Orchestrates the CrewAI content-opportunity scout for one brand.

Two-hop pipeline: the Trends stage (`lib.crew.trends`) surfaces a deduped,
keyword-level list of brand-relevant trend signals by merging real GSC
ranking data, live web search (Serper), and the Instagram trends feed; the
Idea stage (`lib.crew.idea`) then synthesizes those signals into a final,
deduped, ranked list of concrete publishable content ideas. Winners are
written to `content_ideas` (`lib.ideas_db.insert_idea`), same table and row
shape `lib.gsc_scout.run_scout` already writes to (`lib.crew.scout_rows`) so
downstream consumers (`content-enricher`, `wp-post-creator`,
`performance-tracker`) see one consistent shape regardless of which scout
produced a row. Tagged `"source": "crewai_scout"` in `input` so the two are
distinguishable later.

  1. `lib.gsc_scout_scoring.rank_opportunities` -- real GSC ranking data,
     already cannibalization-checked (read via the same building blocks
     `lib.gsc_scout.run_scout` uses; this module never calls `run_scout`
     itself, so the two scouts never double-insert the same GSC opportunity).
  2. Live web search (Serper, via the Trends agent's `SerperDevTool`) for
     topics not already in the brand's keyword seed list.
  3. The public Instagram trends feed (`lib.crew.trends.instagram_scraper`),
     when available.

Every external call (both CrewAI `kickoff()`s, `insert_idea`) is wrapped so
a failure is logged and this never raises out to the caller -- matching
`lib.ideas_db`/`lib.gsc_scout`'s "never crash the run" convention. Tradeoff:
running Trends and Idea as two independent LLM round-trips means a
Trends-stage failure now short-circuits before the Idea stage ever runs,
where today's single combined call was more failure-tolerant -- an accepted,
documented tradeoff, not a bug.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from crewai import Agent, Task

from lib import ideas_db
from lib.crew import keyword_store
from lib.crew.context import (
    brand_identity_summary,
    brand_voice_summary,
    seed_keywords_summary,
    serialize_opportunities,
)
from lib.crew.idea import build_idea_agent, build_idea_task, execute_idea_crew
from lib.crew.idea.prompts import (
    build_idea_task_description,
    serialize_existing_topics,
    serialize_trend_signals,
)
from lib.crew.models import IdeaCandidate, ScoutOutput
from lib.crew.scout_rows import idea_row, read_config
from lib.crew.topic_similarity import is_similar_topic
from lib.crew.trends import (
    TrendsOutput,
    build_trends_agent,
    build_trends_task,
    execute_trends_crew,
)
from lib.crew.trends.instagram_scraper import fetch_instagram_trends_text
from lib.crew.trends.prompts import build_trends_task_description
from lib.gsc_data_sufficiency import evaluate_data_sufficiency
from lib.gsc_scout import load_keyword_seeds, load_site_content_cache
from lib.gsc_scout_scoring import rank_opportunities
from lib.observability import get_logger
from lib.published_content_db.repository import PublishedContentRepository

logger = get_logger(__name__)

TrendsExecuteFn = Callable[[Agent, Task], TrendsOutput | None]
IdeaExecuteFn = Callable[[Agent, Task], ScoutOutput | None]


def run_crew_scout(
    brand_dir: Path,
    *,
    repo: PublishedContentRepository | None = None,
    insert_idea_fn: Callable[..., str | None] | None = None,
    existing_topics_fn: Callable[..., set[str]] | None = None,
    trends_execute_fn: TrendsExecuteFn | None = None,
    idea_execute_fn: IdeaExecuteFn | None = None,
    instagram_trends_fn: Callable[[Path], str | None] | None = None,
    update_status_fn: Callable[[str, str], bool] | None = None,
    top_n: int = 10,
    dry_run: bool = False,
    auto_approve_threshold: float | None = 85.0,
    similarity_threshold: float = 0.85,
) -> list[tuple[IdeaCandidate, str | None]]:
    """Score + discover content opportunities for one brand and write them to
    `content_ideas`.

    Returns `(idea, idea_id)` pairs; `idea_id` is `None` for a duplicate
    topic, a dry run, or a failed insert. Returns `[]` (logged, not raised)
    if either CrewAI hop fails or returns no structured output.

    Ideas scoring `>= auto_approve_threshold` are auto-approved (status set
    to `"approved"`) immediately after insert -- pass `None` to disable.
    Never fires on a `dry_run` (no insert happens, so there's no `idea_id`
    to update). `similarity_threshold` controls the fuzzy-dedup check
    against existing topics (see `lib.crew.topic_similarity.is_similar_topic`).
    """
    brand_id = brand_dir.name
    config = read_config(brand_dir)
    brand_name = str(config.get("site", {}).get("name") or brand_id)

    repo = repo or PublishedContentRepository()
    insert_idea_fn = insert_idea_fn or ideas_db.insert_idea
    existing_topics_fn = existing_topics_fn or ideas_db.existing_topics
    trends_execute_fn = trends_execute_fn or execute_trends_crew
    idea_execute_fn = idea_execute_fn or execute_idea_crew
    instagram_trends_fn = instagram_trends_fn or fetch_instagram_trends_text
    update_status_fn = update_status_fn or ideas_db.update_status

    # Curated vocabulary (the operator's `content_analysis.keywords`) widened
    # by what previous runs' Trends stage discovered. Without this the scout
    # searches one frozen list forever and converges on the same subjects --
    # see `lib.crew.keyword_store` for why the discoveries live in their own
    # file rather than being appended to config.json.
    curated = load_keyword_seeds(brand_dir)
    seeds = curated + keyword_store.active_seeds(
        brand_dir, exclude={s.keyword.strip().lower() for s in curated}
    )
    site_posts = list(load_site_content_cache(brand_dir).get("recent_posts", []) or [])
    published_rows = repo.list_for_brand(brand_id, limit=10_000)
    sufficiency = evaluate_data_sufficiency(
        [(str(r["gsc_query"]), float(r["position"]), int(r["impressions"])) for r in published_rows]
    )
    opportunities = rank_opportunities(
        seeds, published_rows, site_posts, data_sufficient=sufficiency.sufficient, top_n=top_n
    )

    identity = brand_identity_summary(config)
    voice = brand_voice_summary(brand_dir)
    seed_kw = seed_keywords_summary(seeds)

    # `instagram_trends_fn` never raises per its own contract (logs and
    # returns `None` on any scrape failure) -- no extra try/except needed.
    instagram_trends_text = instagram_trends_fn(brand_dir)

    trends_description = build_trends_task_description(
        identity=identity,
        seed_keywords=seed_kw,
        opportunities_json=serialize_opportunities(opportunities),
        data_sufficient=sufficiency.sufficient,
        instagram_trends_text=instagram_trends_text,
    )
    trends_agent = build_trends_agent()
    trends_task = build_trends_task(trends_agent, trends_description)
    try:
        trends_output = trends_execute_fn(trends_agent, trends_task)
    except Exception as exc:  # defense in depth: execute_trends_crew (the real
        # default) already catches internally, but this also protects against
        # any custom `trends_execute_fn` a caller injects that doesn't -- same
        # "never crash the run" contract as `lib.gsc_scout.run_scout`.
        logger.warning("crew_scout_trends_execute_fn_raised", brand_id=brand_id, error=str(exc))
        return []
    if trends_output is None:
        logger.warning("crew_scout_run_no_trends", brand_id=brand_id)
        return []

    logger.info(
        "crew_scout_trends_found",
        brand_id=brand_id,
        signal_count=len(trends_output.signals),
        signals=[s.model_dump() for s in trends_output.signals],
    )

    # Keep what the research found so the NEXT run searches a wider space.
    # Never raises; a failed write costs vocabulary, not this run's ideas.
    # Recorded even on a dry run: discovering a keyword is not the same act as
    # inserting an idea, and a dry run's whole point is to see what a real one
    # would find -- discarding that would make `--dry-run` quietly lossy.
    discovered_new, discovered_seen = keyword_store.record_signals(
        brand_dir, list(trends_output.signals)
    )
    logger.info(
        "crew_scout_keywords_recorded",
        brand_id=brand_id,
        new=discovered_new,
        updated=discovered_seen,
    )

    # Fetched BEFORE the crew runs, not after -- it used to only filter output
    # the agent produced blind. See `build_idea_task_description` for why.
    existing_topics = existing_topics_fn(brand_id=brand_id)

    idea_description = build_idea_task_description(
        identity=identity,
        voice=voice,
        seed_keywords=seed_kw,
        trends_json=serialize_trend_signals(trends_output.signals),
        existing_topics=serialize_existing_topics(existing_topics),
        top_n=top_n,
    )
    idea_agent = build_idea_agent()
    idea_task = build_idea_task(idea_agent, idea_description)
    try:
        output = idea_execute_fn(idea_agent, idea_task)
    except Exception as exc:  # defense in depth: execute_idea_crew (the real
        # default) already catches internally, but this also protects against
        # any custom `idea_execute_fn` a caller injects that doesn't -- same
        # "never crash the run" contract as `lib.gsc_scout.run_scout`.
        logger.warning("crew_scout_idea_execute_fn_raised", brand_id=brand_id, error=str(exc))
        return []
    if output is None:
        logger.warning("crew_scout_run_no_output", brand_id=brand_id)
        return []

    results: list[tuple[IdeaCandidate, str | None]] = []
    for idea in output.ideas:
        if is_similar_topic(idea.topic, existing_topics, threshold=similarity_threshold):
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
        try:
            idea_id = insert_idea_fn(
                idea_row(idea, data_sufficient=sufficiency.sufficient),
                brand_id=brand_id,
                brand_name=brand_name,
            )
        except Exception as exc:  # defense in depth, same rationale as the
            # trends/idea `execute_fn` wrappers above -- treated as a failed
            # insert so the rest of the batch keeps processing.
            logger.warning(
                "crew_scout_insert_idea_fn_raised",
                brand_id=brand_id,
                topic=idea.topic,
                error=str(exc),
            )
            idea_id = None
        if (
            idea_id is not None
            and auto_approve_threshold is not None
            and idea.priority_score >= auto_approve_threshold
        ):
            try:
                approved = update_status_fn(idea_id, "approved")
            except Exception as exc:  # defense in depth, same rationale as above --
                # insert already succeeded, so this only affects the auto-approve
                # attempt, not `idea_id`/`results`.
                logger.warning(
                    "crew_scout_idea_auto_approve_raised",
                    brand_id=brand_id,
                    idea_id=idea_id,
                    topic=idea.topic,
                    error=str(exc),
                )
            else:
                if approved:
                    logger.info(
                        "crew_scout_idea_auto_approved",
                        brand_id=brand_id,
                        idea_id=idea_id,
                        topic=idea.topic,
                        priority_score=idea.priority_score,
                    )
                else:
                    logger.warning(
                        "crew_scout_idea_auto_approve_failed",
                        brand_id=brand_id,
                        idea_id=idea_id,
                        topic=idea.topic,
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
