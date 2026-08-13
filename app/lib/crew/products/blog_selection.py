"""Pick affiliate products for an ALREADY-PUBLISHED post.

The backfill-time sibling of `lib.crew.products.selector`, differing in one
critical way: **there is no flat-catalog fallback**.
`select_products_for_post` answers "what catalog should the writer see?",
so a failed LLM call there degrades to the brand's whole flat catalog --
harmless, because the writer then decides what (if anything) to reference.
Here the answer is written straight into a live post, so the same fallback
would staple four unrelated products (GPS collars, cooling vests) onto
whatever post happened to hit an API error. Instead:

    None  -> the selector failed; the caller SKIPS the post untouched.
    {}    -> the selector judged that nothing fits; also a skip, but a
             correct, deliberate one.

The other difference is variety handling. `selector` hard-excludes
recently-used keys (a fresh post can wait for a product to age out); a
one-shot sweep over every published post can't afford exclusions, or the
tail of the run has nothing left to choose from. So in-run usage counts
only ORDER the candidate list (least-used first) -- a bias the model may
override when a heavily-used product is genuinely the right pick.
"""

from __future__ import annotations

from collections.abc import Mapping

from lib.affiliate_resolver import ProductEntry
from lib.crew.products.agent import build_product_selector_agent, build_product_selector_task
from lib.crew.products.execute import execute_product_selector_crew
from lib.crew.products.prompts import build_selector_task_description
from lib.crew.products.selector import MAX_PRODUCTS, SelectorExecuteFn
from lib.crew.writer.models import ContentBrief
from lib.observability import get_logger

logger = get_logger(__name__)


def candidates_text(
    candidates: Mapping[str, ProductEntry],
    *,
    usage_counts: Mapping[str, int] | None = None,
) -> str:
    """Candidates as `key | display | category | notes` lines -- the exact
    format `build_selector_task_description` documents and
    `lib.crew.products.selector._candidates_text` emits.

    Ordering is `(times used this run, key)`, so unused products lead the
    list and the key tiebreak keeps the prompt deterministic.
    """
    counts = usage_counts or {}
    ordered = sorted(candidates.values(), key=lambda entry: (counts.get(entry.key, 0), entry.key))
    return "\n".join(
        f"{entry.key} | {entry.display} | {entry.category or ''} | {entry.notes or ''}"
        for entry in ordered
    )


def select_products_for_existing_post(
    pool: Mapping[str, ProductEntry],
    brief: ContentBrief,
    *,
    usage_counts: Mapping[str, int] | None = None,
    execute_fn: SelectorExecuteFn | None = None,
    max_products: int = MAX_PRODUCTS,
) -> dict[str, ProductEntry] | None:
    """At most `max_products` genuine fits for `brief` from `pool`.

    Returns `None` when the selector call itself failed (skip the post),
    `{}` when the selector legitimately found no fit (also skip), and
    otherwise the picked entries in the model's own order. Keys the model
    returns that aren't in `pool` are dropped with a warning -- same
    "don't fully trust the model" posture as
    `lib.crew.writer.context.sanitize_internal_links`.
    """
    if not pool:
        logger.info("blog_products_pool_empty")
        return {}

    # Already-used products are REMOVED, not merely ranked last. Ordering
    # alone was proven insufficient: `iris-weatherpro-33qt` was placed on two
    # storage-adjacent posts (4289, 4295) because the model still judged it
    # the best fit no matter where it sat in the list. A candidate the model
    # cannot see is a repeat it cannot make.
    #
    # The exclusion relaxes back to the full pool when it would leave too few
    # to choose from -- the same guard `selector.select_products_for_post`
    # uses, and the reason a small catalog isn't starved into empty prompts.
    candidates = {key: entry for key, entry in pool.items() if not (usage_counts or {}).get(key)}
    if len(candidates) < max_products:
        logger.info(
            "blog_products_dedupe_relaxed",
            candidate_count=len(candidates),
            pool_size=len(pool),
        )
        candidates = dict(pool)

    description = build_selector_task_description(
        brief=brief,
        candidates_text=candidates_text(candidates, usage_counts=usage_counts),
        max_products=max_products,
    )
    agent = build_product_selector_agent()
    task = build_product_selector_task(agent, description)
    selection = (execute_fn or execute_product_selector_crew)(agent, task)

    if selection is None:
        logger.warning("blog_products_selector_failed", title=brief.suggested_title)
        return None

    picked: dict[str, ProductEntry] = {}
    for product in selection.products:
        # Validated against `candidates`, not `pool`: an excluded product is
        # not a legal pick, so a model that names one anyway (from an earlier
        # turn, or invention) must be refused rather than quietly re-admitted.
        if product.key not in candidates:
            logger.warning("blog_products_unknown_key_dropped", key=product.key)
            continue
        if product.key in picked:
            continue
        picked[product.key] = candidates[product.key]
        if len(picked) >= max_products:
            break
    logger.info("blog_products_selected", keys=list(picked))
    return picked
