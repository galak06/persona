"""Selection orchestration: pick the affiliate products for ONE planned post.

The one composition point of this subpackage: merge the brand's catalogs
into a candidate pool (`lib.crew.products.pool`), exclude recently featured
products (`lib.crew.products.usage`), ask the selector agent to curate at
most `MAX_PRODUCTS` genuine fits for the post's plan, then defensively
validate the model's picks against the real candidate set -- same "don't
fully trust the model" posture as
`lib.crew.writer.context.sanitize_internal_links`.

Failure posture is deliberately asymmetric:
  - selector failure (`None` from the execute seam -- LLM/parse error) falls
    back to the FLAT brand catalog unchanged, i.e. exactly the pre-selector
    behavior of `lib.crew.writer.orchestrator.write_post_from_brief`;
  - an EMPTY (but valid) selection returns `{}` -- the agent's judged
    "nothing in the catalog fits this post", which the writer prompt's
    empty-catalog branch already handles correctly.

`SelectorExecuteFn` lives here at the caller level (not in
`lib.crew.products.execute`), matching how `lib.crew.draft_category` places
`CategorizeFn` next to its own `categorize_fn` seam.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from crewai import Agent, Task

from lib.affiliate_resolver import ProductEntry
from lib.crew.products.agent import build_product_selector_agent, build_product_selector_task
from lib.crew.products.execute import execute_product_selector_crew
from lib.crew.products.models import ProductSelection
from lib.crew.products.pool import load_candidate_pool
from lib.crew.products.prompts import build_selector_task_description
from lib.crew.products.usage import recently_used_keys
from lib.crew.writer.context import load_brand_affiliate_catalog
from lib.crew.writer.models import ContentBrief
from lib.observability import get_logger

logger = get_logger(__name__)

MAX_PRODUCTS = 4

SelectorExecuteFn = Callable[[Agent, Task], ProductSelection | None]


def _candidates_text(candidates: dict[str, ProductEntry]) -> str:
    """Render candidates as `key | display | category | notes` lines (the
    format `build_selector_task_description` documents), sorted by key for
    prompt determinism. `None` fields render as empty strings."""
    lines = []
    for key in sorted(candidates):
        entry = candidates[key]
        lines.append(f"{key} | {entry.display} | {entry.category or ''} | {entry.notes or ''}")
    return "\n".join(lines)


def select_products_for_post(
    brand_dir: Path,
    brief: ContentBrief,
    *,
    execute_fn: SelectorExecuteFn | None = None,
    usage_path: Path | None = None,
) -> dict[str, ProductEntry]:
    """The catalog the writer stage should see for THIS post: at most
    `MAX_PRODUCTS` selector-curated entries from the merged candidate pool.

    Recently featured products (`lib.crew.products.usage`) are excluded from
    the candidates unless that would leave fewer than `MAX_PRODUCTS` to
    choose from -- a small catalog shouldn't be starved into empty prompts,
    so the exclusion relaxes back to the full pool (logged).
    """
    pool = load_candidate_pool(brand_dir)
    if not pool:
        logger.info("crew_products_pool_empty", brand_id=brand_dir.name)
        return {}

    used = recently_used_keys(path=usage_path)
    candidates = {key: entry for key, entry in pool.items() if key not in used}
    if len(candidates) < MAX_PRODUCTS:
        logger.info(
            "crew_products_dedupe_relaxed",
            candidate_count=len(candidates),
            pool_size=len(pool),
            recently_used_count=len(used),
        )
        candidates = dict(pool)

    description = build_selector_task_description(
        brief=brief,
        candidates_text=_candidates_text(candidates),
        max_products=MAX_PRODUCTS,
    )
    agent = build_product_selector_agent()
    task = build_product_selector_task(agent, description)
    selection = (execute_fn or execute_product_selector_crew)(agent, task)

    if selection is None:
        # LLM/parse failure: fall back to the flat brand catalog unchanged --
        # exactly the writer stage's pre-selector behavior.
        logger.warning("crew_products_selector_fallback", brand_id=brand_dir.name)
        return load_brand_affiliate_catalog(brand_dir)

    picked: dict[str, ProductEntry] = {}
    for product in selection.products:
        if product.key not in candidates:
            logger.warning("crew_products_unknown_key_dropped", key=product.key)
            continue
        if product.key in picked:
            continue
        picked[product.key] = candidates[product.key]
        if len(picked) >= MAX_PRODUCTS:
            break
    logger.info("crew_products_selected", keys=list(picked))
    return picked
