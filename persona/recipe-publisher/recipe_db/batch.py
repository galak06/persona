"""Batch crawl of a recipe listing/hub page into the recipe DB.

Fetches a category page, extracts canonical recipe links, scrapes each recipe,
and stores it. The network fetcher is injected so the whole flow is
unit-testable offline.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from recipe_db import db, normalize, safety, scraper, seed_exporter
from recipe_db.models import RecipeRow, RecipeStatus, ScrapedRecipe, slugify
from recipe_db.rename import Namer
from recipe_db.repository import RecipeRepository

logger = logging.getLogger("recipe_db.batch")

Fetcher = Callable[[str], str]

# Outcome status values for a single candidate recipe.
STORED = "stored"
EXPORTED = "exported"
WOULD_STORE = "would_store"
NO_RECIPE = "no_recipe"
ERROR = "error"


@dataclass
class RecipeOutcome:
    """Result of processing a single candidate recipe URL."""

    url: str
    status: str
    id: str = ""
    name: str = ""
    dog_safe: bool = False
    detail: str = ""


@dataclass
class BatchSummary:
    """Aggregate result of a category crawl."""

    category_url: str
    found_links: int = 0
    outcomes: list[RecipeOutcome] = field(default_factory=list)

    def count(self, status: str) -> int:
        """How many outcomes ended in ``status``."""
        return sum(1 for outcome in self.outcomes if outcome.status == status)


def _to_row(scraped: ScrapedRecipe) -> tuple[RecipeRow, list[str], bool]:
    """Build a safety-checked RecipeRow from a normalized recipe."""
    flags, dog_safe = safety.scan_ingredients(scraped.ingredients)
    row = RecipeRow(
        name=scraped.name,
        ingredients=scraped.ingredients,
        steps=scraped.steps,
        prep_minutes=scraped.prep_minutes,
        cook_minutes=scraped.cook_minutes,
        total_minutes=scraped.total_minutes,
        servings=scraped.servings,
        nutrition=scraped.nutrition,
        category=scraped.category,
        tags=scraped.tags,
        hero_image_url=scraped.hero_image_url,
        source_url=scraped.source_url,
        source_name=scraped.source_name,
        license=scraped.license,
        content_hash=scraped.content_hash,
        id=slugify(scraped.name),
        status=RecipeStatus.SAFETY_CHECKED,
        toxic_flags=flags,
        dog_safe=dog_safe,
    )
    return row, flags, dog_safe


def _process_one(
    url: str,
    *,
    dry_run: bool,
    do_export: bool,
    fetch: Fetcher,
    repo: RecipeRepository | None,
    now_iso: str,
    namer: Namer | None,
) -> RecipeOutcome:
    """Scrape and (optionally) store a single recipe URL."""
    raw = scraper.scrape(url, html=fetch(url))
    if raw is None:
        return RecipeOutcome(url, NO_RECIPE, detail="no schema.org Recipe")
    scraped = normalize.normalize(raw, url)
    if not scraped.name:
        return RecipeOutcome(url, NO_RECIPE, detail="recipe has no name")

    row, _flags, dog_safe = _to_row(scraped)
    if namer is not None and not dry_run:
        row.display_name = namer(
            scraped.name, [ing.item for ing in scraped.ingredients]
        )
    base = RecipeOutcome(
        url, WOULD_STORE, id=row.id, name=row.name, dog_safe=dog_safe,
    )
    if dry_run or repo is None:
        return base

    repo.insert_raw(
        source_url=row.source_url, source_name=row.source_name,
        payload=raw, content_hash=row.content_hash, scraped_at=now_iso,
    )
    repo.upsert_recipe(row)
    base.status = STORED
    if do_export and dog_safe:
        seed_exporter.export_seed(row)
        repo.set_status(row.id, RecipeStatus.SEED_EXPORTED)
        base.status = EXPORTED
    return base


def scrape_category(
    category_url: str,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    do_export: bool = False,
    delay_seconds: float = 1.0,
    fetch: Fetcher = scraper.fetch_html,
    now_iso: str = "",
    repo: RecipeRepository | None = None,
    namer: Namer | None = None,
) -> BatchSummary:
    """Crawl ``category_url`` and store every recipe found on it.

    A polite ``delay_seconds`` pause separates recipe fetches. When ``repo`` is
    omitted and not a dry run, a DB connection is opened and closed internally.
    """
    summary = BatchSummary(category_url=category_url)
    links = scraper.extract_recipe_links(fetch(category_url), category_url)
    if limit is not None:
        links = links[:limit]
    summary.found_links = len(links)
    logger.info("found %d recipe links on %s", len(links), category_url)

    own_conn = None
    if not dry_run and repo is None:
        own_conn = db.connect()
        db.migrate(own_conn)
        repo = RecipeRepository(own_conn)
    try:
        for index, url in enumerate(links):
            try:
                outcome = _process_one(
                    url, dry_run=dry_run, do_export=do_export, fetch=fetch,
                    repo=repo, now_iso=now_iso, namer=namer,
                )
            except Exception as exc:  # one bad page must not abort the crawl
                logger.warning("error processing %s: %s", url, exc)
                outcome = RecipeOutcome(url, ERROR, detail=str(exc))
            summary.outcomes.append(outcome)
            if delay_seconds and index < len(links) - 1:
                time.sleep(delay_seconds)
    finally:
        if own_conn is not None:
            own_conn.close()
    return summary
