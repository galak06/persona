"""Guarantee that a drafted post carries the products the selector chose,
and the compliance surface those products legally require.

The selector's picks reach the writer as *suggestions* inside its prompt,
and the writer is free to ignore them -- which is not hypothetical: the
first post drafted after the pipeline was unblocked came out with an
affiliate disclosure, an "Our Pick" heading, and zero products, because the
model referenced none of the four it was handed. Nothing downstream noticed,
because `[AFFILIATE:key]` resolution can only rewrite placeholders that were
actually written.

So placement stops depending on the writer's cooperation. If the finished
body already links a picked product, it is left exactly as written -- an
in-context recommendation reads better than an appended block. Only when the
body links none of them does this append the same marker-wrapped block
`scripts/backfill_blog_product_blocks.py` already puts on published posts,
via the same `lib.crew.products.block` renderer (no prices, correct tag,
per-slug `ascsubtag`).

That editorial judgement was right and it stays. What was wrong is that
"leave it as written" ALSO dropped `rel="sponsored nofollow"`, the
`ascsubtag` campaign id and the Amazon Associates statement, because those
existed only inside the block -- so every post whose writer linked one
catalog product inline shipped non-compliant (14 of them, 2026-08-14 to
2026-08-19). Placement is now the only conditional part: whichever branch
runs, the result goes through `lib.crew.products.compliance`, so the
compliance surface is unconditional by construction rather than by luck.

Never raises: a missing Associates tag or an empty pick list leaves
placement alone, since a naked Amazon link would violate the Associates T&C
and a half-rendered block is worse than none.
"""

from __future__ import annotations

import os
import re

from lib.affiliate_resolver import ProductEntry
from lib.crew.products.block import BLOG_BLOCK_OPEN, render_blog_block
from lib.crew.products.compliance import enforce_affiliate_compliance, slug_for
from lib.crew.writer.models import ContentBrief
from lib.observability import get_logger

logger = get_logger(__name__)

_ASIN_IN_BODY_RE = re.compile(r"/dp/([A-Z0-9]{10})")
_PLACEHOLDER_RE = re.compile(r"\[AFFILIATE:([a-z0-9][a-z0-9_-]*)\]", re.IGNORECASE)


def _already_referenced(body_html: str, catalog: dict[str, ProductEntry]) -> list[str]:
    """Keys from `catalog` the body already links, by unresolved placeholder
    or by a resolved `/dp/<ASIN>` URL (the writer's placeholders may already
    have been substituted upstream)."""
    placeholders = {m.group(1).lower() for m in _PLACEHOLDER_RE.finditer(body_html)}
    asins = {m.group(1) for m in _ASIN_IN_BODY_RE.finditer(body_html)}
    return [
        key for key, entry in catalog.items() if key.lower() in placeholders or entry.asin in asins
    ]


def _place_products(
    body_html: str,
    catalog: dict[str, ProductEntry],
    slug: str,
) -> tuple[str, list[str]]:
    """`(body_html, keys_placed)` -- placement only, no compliance pass."""
    if not catalog:
        return body_html, []

    referenced = _already_referenced(body_html, catalog)
    if referenced:
        logger.info("crew_products_block_skipped_already_linked", keys=referenced)
        return body_html, referenced
    if BLOG_BLOCK_OPEN in body_html:
        logger.info("crew_products_block_already_present")
        return body_html, []

    tag = os.environ.get("AMAZON_ASSOCIATES_TAG", "").strip()
    if not tag:
        # Same refusal `render_blog_block` enforces, made explicit here so the
        # cause is one log line rather than a swallowed ValueError.
        logger.warning("crew_products_block_skipped_no_associates_tag")
        return body_html, []

    products = list(catalog.values())
    try:
        block = render_blog_block(products, slug, associates_tag=tag)
    except ValueError as exc:
        logger.warning("crew_products_block_render_failed", error=str(exc))
        return body_html, []
    if not block:
        return body_html, []

    logger.info("crew_products_block_appended", keys=[p.key for p in products])
    return f"{body_html.rstrip()}\n\n{block}\n", [p.key for p in products]


def ensure_product_block(
    body_html: str,
    catalog: dict[str, ProductEntry],
    brief: ContentBrief,
    *,
    slug: str | None = None,
) -> tuple[str, list[str]]:
    """`(body_html, keys_placed)` -- the body with a picks block appended when
    it referenced none of the selector's products, and with every Amazon link
    made compliant either way (`lib.crew.products.compliance`).

    `keys_placed` is what actually ended up in the post (already-referenced
    keys, or the block's keys), so the caller records real usage rather than
    what the writer claimed.

    `slug` is the post's slug WITHOUT the `blog-` campaign prefix; it defaults
    to one derived from the brief's title. Pass the title WordPress will
    actually receive when the writer may have retitled the post, so the
    block's `ascsubtag` and the inline links' agree. Never pass a WordPress
    `slug` field -- see `compliance.slug_for`.
    """
    campaign_slug = slug or slug_for(brief.suggested_title)
    body, keys = _place_products(body_html, catalog, campaign_slug)
    return enforce_affiliate_compliance(body, campaign_slug), keys
