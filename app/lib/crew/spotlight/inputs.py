"""Everything one spotlight is composed FROM, before any image exists.

`compose.py` is the transaction -- guard the row, generate, commit, mark -- and
it was 390 lines with this material inlined, well past the repo's 300-line
ceiling. The seam is the natural one: this module gathers and drafts (the live
post, the affiliate links, the writer plan, the reviewer's flags) and knows
nothing about files, images or row states; `compose.py` owns the row and
everything that costs an image call.

Nothing here writes to the database or to disk, so each piece is testable on
its own, and a failure is returned as a machine-readable reason string from the
frozen vocabulary rather than raised -- the caller is the only thing that knows
which row to write it against.
"""

from __future__ import annotations

import os
from functools import partial
from pathlib import Path
from typing import Any

import httpx

from lib.affiliate_resolver import ProductEntry
from lib.crew import wp_source
from lib.crew.brand_identity import site_domain
from lib.crew.context import brand_voice_summary
from lib.crew.products.social_links import spotlight_affiliate_url
from lib.crew.reference_vocabulary import category_menu
from lib.crew.socialpost import (
    build_social_post_agent,
    build_social_post_task,
    execute_social_post_crew,
)
from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.spotlight.post_products import product_excerpt
from lib.crew.spotlight.prompts import build_spotlight_task_description, spotlight_keyword
from lib.crew.spotlight.rules import (
    FLAG_PRODUCT_NOT_IN_POST,
    find_spotlight_flags,
    find_spotlight_violations,
)
from lib.errors import ConfigurationError
from lib.medical_claims_validator import find_banned_claims
from lib.observability import get_logger

logger = get_logger(__name__)

#: "WordPress could not be reached", as `lib.crew.wp_source` fails: missing
#: credentials raise `ConfigurationError` out of `wp_client()`, network or HTTP
#: trouble raises through httpx, a malformed base URL raises `ValueError`. Same
#: tuple as `api.social_derivatives_support.WP_UNAVAILABLE`: `lib` may not
#: import `api`, and repeating three names beats inverting the layers.
WP_UNAVAILABLE = (ConfigurationError, httpx.HTTPError, ValueError)


def post_html(post: dict[str, Any] | None) -> str:
    """`post["content"]["rendered"]`, defensively -- WP omits `content` for a
    post whose metadata the credentials may read but whose body they may not."""
    if not post:
        return ""
    content = post.get("content")
    return str(content.get("rendered") or "") if isinstance(content, dict) else ""


def fetch_post(idea: dict[str, Any]) -> dict[str, Any] | str:
    """The live post, or a failure reason.

    Two "no post" stories with different fixes: `no_wp_post` means the idea was
    never published (the API filters those out, so reaching it here is a race),
    while `wp_fetch_failed` means the container's WP credentials or the site
    itself are the problem.
    """
    wp_post_id = str(idea.get("wp_post_id") or "").strip()
    if not wp_post_id:
        return "no_wp_post"
    try:
        post = wp_source.fetch_post(wp_post_id)
    except WP_UNAVAILABLE as exc:
        logger.warning("spotlight_wp_fetch_failed", error=f"{type(exc).__name__}: {exc}"[:200])
        return "wp_fetch_failed"
    return post if post is not None else "wp_fetch_failed"


def affiliate_urls(product: ProductEntry, slug: str) -> tuple[str, str] | str:
    """`(fb_url, ig_url)`, or a failure reason.

    The associates tag is read from the environment here rather than passed in
    because it IS environment, not state: the script pre-flights it, and this
    second read is what keeps the module honest when it is called from
    anywhere else. An untagged Amazon link handed out under an affiliate
    disclosure states a commercial relationship the link does not carry, so a
    missing tag fails the whole composition rather than shipping a bare link.
    """
    tag = os.environ.get("AMAZON_ASSOCIATES_TAG", "").strip()
    if not tag:
        return "associates_tag_missing"
    try:
        return (
            spotlight_affiliate_url(product.asin, tag, channel="fb", slug=slug),
            spotlight_affiliate_url(product.asin, tag, channel="ig", slug=slug),
        )
    except ValueError as exc:
        # A blank ASIN or slug -- the row is unusable, and a spotlight without
        # a link is just an ad the reader cannot act on.
        logger.warning("spotlight_affiliate_url_failed", error=str(exc)[:200])
        return "affiliate_url_failed"


def build_plan(
    post: dict[str, Any],
    *,
    product: ProductEntry,
    brand_dir: Path,
    mascot_facts: str,
    photo_is_owner_pick: bool,
    product_in_post: bool,
) -> SocialPostPlan | None:
    """One writer run: the regular social-post crew under the spotlight brief.

    `extra_violations` puts the affiliate rules inside the SAME retry loop the
    caption rules use, so a draft that drops its disclosure or invents a
    certification is handed back for a minimal correction rather than discarded
    after it has already been paid for.
    """
    html = post_html(post)
    categories, category_photos = category_menu(brand_dir)
    agent = build_social_post_agent()
    description = build_spotlight_task_description(
        title=str((post.get("title") or {}).get("rendered") or ""),
        body=product_excerpt(wp_source.strip_html(html), product),
        product=product,
        site_domain=site_domain(brand_dir),
        brand_voice=brand_voice_summary(brand_dir),
        mascot_facts=mascot_facts,
        photo_is_owner_pick=photo_is_owner_pick,
        product_in_post=product_in_post,
        reference_categories=categories,
        reference_descriptions=category_photos,
    )
    return execute_social_post_crew(
        agent,
        build_social_post_task(agent, description),
        target_keyword=spotlight_keyword(product),
        extra_violations=partial(find_spotlight_violations, product=product),
    )


def review_flags(
    plan: SocialPostPlan,
    *,
    product: ProductEntry,
    mascot_name: str,
    mascot_facts: str,
    product_in_post: bool,
) -> list[str]:
    """Everything the reviewer should see, nothing that should have blocked.

    Three independent sources, concatenated here rather than inside any one of
    them so each stays testable alone: the generic medical/credential scanner
    over both captions, the spotlight's own flag-only rules, and the
    not-in-post marker, which only the compose path knows about.
    """
    found = [
        *find_banned_claims(plan.fb_caption),
        *find_banned_claims(plan.ig_caption),
        *find_spotlight_flags(
            plan, product=product, mascot_name=mascot_name, mascot_facts=mascot_facts
        ),
    ]
    if not product_in_post:
        found.append(FLAG_PRODUCT_NOT_IN_POST)
    # Both captions can carry the same banned phrase; the card should say it once.
    return list(dict.fromkeys(found))
