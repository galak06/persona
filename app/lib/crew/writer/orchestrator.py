"""Orchestrates the strategist -> writer pipeline for one `content_ideas` row.

Sequential, two-stage CrewAI pipeline: `build_content_brief` (strategist)
produces a `ContentBrief`, `write_post_from_brief` (writer) turns that brief
into a full `WrittenPost`, and `assemble_final_html` injects JSON-LD
(`lib.blog_jsonld`) and resolves `[AFFILIATE:key]` placeholders
(`lib.affiliate_resolver.resolve_html`). `strategist_and_writer` composes all
three for convenience; the CLI (`scripts/crewai_content_writer.py`) instead
calls each stage directly so it can report progress and surface the brief
and word count even if affiliate resolution ultimately fails.

Unlike `lib.crew.scout`'s "never crash the run" convention, a failed/absent
strategist or writer output returns `None` (logged), but `assemble_final_html`
deliberately lets `AffiliateResolverError` PROPAGATE -- a broken disclosure/
tag/catalog-key contract is a loud, actionable signal, not something to
swallow. This build never writes to WordPress or `content_ideas`; it only
reads one existing idea row and returns the assembled HTML + metadata.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crewai import Agent, Task

from lib import ideas_db
from lib.crew.context import brand_identity_summary, brand_voice_summary
from lib.crew.mascot_library import list_category_labels
from lib.crew.writer.agent import (
    build_strategist_agent,
    build_strategist_task,
    build_writer_agent,
    build_writer_task,
)
from lib.crew.writer.assemble import assemble_final_html
from lib.crew.writer.context import (
    catalog_summary_text,
    filter_links_to_allowed,
    idea_priority,
    internal_link_candidates_from_cache,
    load_disclosure_text,
    mascot_facts_summary,
    read_brand_config,
    sanitize_internal_links,
)
from lib.crew.writer.execute import execute_strategist_crew, execute_writer_crew
from lib.crew.writer.models import ContentBrief, WrittenPost
from lib.crew.writer.prompts import build_strategist_task_description, build_writer_task_description
from lib.gsc_scout import load_site_content_cache
from lib.observability import get_logger

if TYPE_CHECKING:  # runtime import would be circular -- lib.crew.products
    from lib.crew.products.selector import SelectorExecuteFn  # imports writer.context

logger = get_logger(__name__)

StrategistExecuteFn = Callable[[Agent, Task], ContentBrief | None]
WriterExecuteFn = Callable[[Agent, Task], WrittenPost | None]
ListIdeasFn = Callable[..., list[dict[str, Any]]]
UpdateStatusFn = Callable[[str, str], bool]


@dataclass(frozen=True)
class ContentDraftResult:
    """Everything one pipeline run produced -- returned by `strategist_and_writer`."""

    idea_id: str
    idea_topic: str
    brief: ContentBrief
    written_post: WrittenPost
    final_html: str
    word_count: int
    affiliate_keys_used: list[str]


def select_idea(
    brand_dir: Path,
    *,
    idea_id: str | None = None,
    list_ideas_fn: ListIdeasFn = ideas_db.list_ideas,
) -> dict[str, Any] | None:
    """Pick one `content_ideas` row to draft.

    With `idea_id`: look it up directly across ANY status -- explicit
    targeting for a specific row (review/demo runs, or re-drafting an idea
    that already moved past `publish`).

    Without `idea_id`: the highest-scored `status='publish'` row (see
    `lib.crew.writer.context.idea_priority`), oldest `created_at` first as a tiebreak for
    determinism. Documented choice: both scout sources already rank their
    own candidates by this score, so it's a better proxy for "write this
    next" than raw insertion order.
    """
    brand_id = brand_dir.name
    if idea_id:
        rows = list_ideas_fn(brand_id=brand_id, limit=5000)
        return next((r for r in rows if str(r.get("id")) == idea_id), None)

    rows = list_ideas_fn(status="publish", brand_id=brand_id, limit=500)
    if not rows:
        return None
    return sorted(rows, key=lambda r: (-idea_priority(r), str(r.get("created_at") or "")))[0]


def build_content_brief(
    brand_dir: Path,
    idea: dict[str, Any],
    *,
    execute_fn: StrategistExecuteFn | None = None,
) -> ContentBrief | None:
    """Strategist stage: one idea row -> a sanitized `ContentBrief`."""
    config = read_brand_config(brand_dir)
    site_cache = load_site_content_cache(brand_dir)
    link_candidates = internal_link_candidates_from_cache(site_cache)

    description = build_strategist_task_description(
        idea=idea,
        identity=brand_identity_summary(config),
        voice=brand_voice_summary(brand_dir),
        mascot_facts=mascot_facts_summary(brand_dir),
        link_candidates=link_candidates,
        year=date.today().year,
        # The strategist may only tag the post with a category the brand
        # actually keeps photos under; an empty library drops the section.
        reference_categories=list_category_labels(brand_dir),
    )
    agent = build_strategist_agent()
    task = build_strategist_task(agent, description)
    execute_fn = execute_fn or execute_strategist_crew
    brief = execute_fn(agent, task)
    if brief is None:
        return None

    allowed_urls = {c.url for c in link_candidates}
    return sanitize_internal_links(brief, allowed_urls)


def write_post_from_brief(
    brand_dir: Path,
    brief: ContentBrief,
    *,
    execute_fn: WriterExecuteFn | None = None,
    product_execute_fn: SelectorExecuteFn | None = None,
    discover: bool = False,
) -> WrittenPost | None:
    """Writer stage: a `ContentBrief` -> a `WrittenPost`. Internal links used
    are re-filtered against the real site cache; the writer sees the
    selector-curated per-post catalog (`lib.crew.products.selector`).

    `discover=True` lets that catalog include products found live for this
    post (`lib.crew.products.discovery`). Off by default and opted into only
    by `scripts/crewai_content_pipeline.py`: discovery costs a DeepSeek call
    plus Serper searches, and defaulting it on silently billed the test suite
    for real ones.

    Whatever the catalog ends up being, the selector's picks are guaranteed
    into the body (`lib.crew.products.ensure_block`) rather than left to the
    writer to reference or ignore, and `affiliate_keys_used` is rewritten to
    what actually landed in the post.
    """
    from lib.crew.products import record_usage, select_products_for_post  # break import cycle
    from lib.crew.products.ensure_block import ensure_product_block

    config = read_brand_config(brand_dir)
    site = config.get("site", {}) if isinstance(config, dict) else {}
    catalog = select_products_for_post(
        brand_dir, brief, execute_fn=product_execute_fn, discover=discover
    )

    description = build_writer_task_description(
        brief=brief,
        identity=brand_identity_summary(config),
        voice=brand_voice_summary(brand_dir),
        mascot_facts=mascot_facts_summary(brand_dir),
        catalog_text=catalog_summary_text(catalog),
        disclosure_text=load_disclosure_text(brand_dir),
        persona=str(site.get("brand_persona") or "The Author"),
        today=date.today().strftime("%B %-d, %Y"),
        year=date.today().year,
    )
    agent = build_writer_agent()
    task = build_writer_task(agent, description)
    written_post = (execute_fn or execute_writer_crew)(agent, task)
    if written_post is None:
        return None

    body_html, block_keys = ensure_product_block(written_post.body_html, catalog, brief)
    used_keys = list(dict.fromkeys([*written_post.affiliate_keys_used, *block_keys]))
    if used_keys:
        record_usage(brief.suggested_title, used_keys)

    allowed_urls = {c.url for c in brief.internal_link_candidates}
    filtered_links = filter_links_to_allowed(written_post.internal_links_used, allowed_urls)
    return written_post.model_copy(
        update={
            "internal_links_used": filtered_links,
            "body_html": body_html,
            "affiliate_keys_used": used_keys,
        }
    )


def strategist_and_writer(
    brand_dir: Path,
    *,
    idea_id: str | None = None,
    list_ideas_fn: ListIdeasFn = ideas_db.list_ideas,
    update_status_fn: UpdateStatusFn = ideas_db.update_status,
    strategist_execute_fn: StrategistExecuteFn | None = None,
    writer_execute_fn: WriterExecuteFn | None = None,
    discover: bool = False,
) -> ContentDraftResult | None:
    """Full pipeline: pick an idea, build a brief, write the post, assemble
    the final HTML. Returns `None` (logged) if no idea is found, or if
    either CrewAI stage fails to produce structured output. Lets
    `AffiliateResolverError` propagate out of `assemble_final_html` --
    NOT caught here, matching that function's contract.

    A strategist/writer failure moves the idea to `status='write_failed'`
    (`lib.ideas_db.STATUSES`) -- without this, `select_idea`'s deterministic
    highest-scored-first pick would retry the EXACT same idea forever on
    every subsequent run (live-reproduced: two consecutive real pipeline
    runs both picked the same idea and failed the same way).
    """
    idea = select_idea(brand_dir, idea_id=idea_id, list_ideas_fn=list_ideas_fn)
    if idea is None:
        logger.warning("crew_writer_no_idea_found", brand_id=brand_dir.name, idea_id=idea_id)
        return None
    real_idea_id = str(idea.get("id"))

    brief = build_content_brief(brand_dir, idea, execute_fn=strategist_execute_fn)
    if brief is None:
        logger.warning("crew_writer_strategist_produced_no_brief", idea_id=real_idea_id)
        update_status_fn(real_idea_id, "write_failed")
        return None
    logger.info(
        "crew_writer_brief_ready",
        idea_id=real_idea_id,
        title=brief.suggested_title,
        sections=len(brief.outline),
    )

    written_post = write_post_from_brief(
        brand_dir, brief, execute_fn=writer_execute_fn, discover=discover
    )
    if written_post is None:
        logger.warning("crew_writer_writer_produced_no_post", idea_id=real_idea_id)
        update_status_fn(real_idea_id, "write_failed")
        return None
    logger.info(
        "crew_writer_draft_ready",
        idea_id=idea.get("id"),
        word_count=written_post.word_count,
        affiliate_keys=written_post.affiliate_keys_used,
    )

    final_html = assemble_final_html(brand_dir, written_post)

    logger.info(
        "crew_writer_pipeline_complete",
        idea_id=idea.get("id"),
        word_count=written_post.word_count,
    )
    return ContentDraftResult(
        idea_id=str(idea.get("id")),
        idea_topic=str(idea.get("topic", "")),
        brief=brief,
        written_post=written_post,
        final_html=final_html,
        word_count=written_post.word_count,
        affiliate_keys_used=written_post.affiliate_keys_used,
    )
