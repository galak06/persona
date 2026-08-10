"""Render the blog-facing affiliate picks block + idempotent insert.

The blog counterpart to `lib.recipe_products.block_renderer` (whose marker
/ regex / disclosure pattern this module deliberately copies), with two
deliberate differences:

  1. It renders `lib.affiliate_resolver.ProductEntry` values directly --
     the shape `lib.crew.products.pool.load_candidate_pool` produces --
     rather than `lib.recipe_products.catalog.RecipeProduct`. A
     ProductEntry has an OPTIONAL `notes`/`category` (the recipe product's
     `blurb` is required), so converting one into the other and calling
     `render_block` would crash on `None`.
  2. Its own marker pair (`blog-picks-block:v1`) so a blog block and a
     recipe block can coexist in one post without either replace pass
     eating the other.

Idempotency works exactly like the recipe renderer's: the block is wrapped
in HTML-comment markers and `insert_or_replace_blog_block` replaces
in-place when they're already present, so re-running a backfill never
duplicates a block.

`open_marker`/`close_marker` are overridable so a caller refreshing a post
that already carries a RECIPE block can re-render *between the recipe
markers* (keeping that post's existing wording) instead of stapling a
second block onto it.

Never renders prices: Amazon Associates forbids stating a price that isn't
pulled live from their API.
"""

from __future__ import annotations

import html as _html
import re
from collections.abc import Sequence
from typing import Final

from lib.affiliate_resolver import ProductEntry

BLOG_BLOCK_OPEN: Final[str] = "<!-- blog-picks-block:v1 -->"
BLOG_BLOCK_CLOSE: Final[str] = "<!-- /blog-picks-block -->"

#: Blog wording -- deliberately NOT the recipe block's "Tools Used in This
#: Recipe" phrasing, which reads wrong on a non-recipe post.
BLOG_HEADING: Final[str] = "Gear I Actually Use"
BLOG_INTRO: Final[str] = "A few things that hold up in real life with Nalla:"

#: Same text as the recipe renderer's disclosure, and it deliberately
#: contains "As an Amazon Associate" -- one of the tokens
#: `lib.affiliate_resolver._has_disclosure` looks for.
DISCLOSURE: Final[str] = (
    "As an Amazon Associate, I earn from qualifying purchases. "
    "We only recommend gear we actually use."
)

# Match the whole block whether or not it has surrounding whitespace
# (copied from lib.recipe_products.block_renderer._BLOCK_RE).
_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    re.escape(BLOG_BLOCK_OPEN) + r".*?" + re.escape(BLOG_BLOCK_CLOSE),
    re.DOTALL,
)

# FAQ heading -- case-insensitive, matches "FAQ", "FAQs", "Frequently Asked
# Questions" (copied from lib.recipe_products.block_renderer).
_FAQ_HEADING_RE: Final[re.Pattern[str]] = re.compile(
    r"<h2\b[^>]*>\s*(?:FAQ[s]?|Frequently\s+Asked\s+Questions?)\b[^<]*</h2>",
    re.IGNORECASE,
)


def _affiliate_url(asin: str, tag: str, slug: str) -> str:
    """`blog-` ascsubtag prefix so Amazon reporting separates these
    placements from the recipe block's `recipe-` ones."""
    return f"https://www.amazon.com/dp/{asin}?tag={tag}&ascsubtag=blog-{slug}"


def _detail_text(product: ProductEntry) -> str:
    """The "why this product" line. `notes` is the curated one-liner;
    `category` is a weak fallback; BOTH are optional on a `ProductEntry`,
    so this coalesces `None` to `""` rather than rendering "None"."""
    return (product.notes or product.category or "").strip()


def render_blog_block(
    products: Sequence[ProductEntry],
    slug: str,
    *,
    associates_tag: str,
    heading: str = BLOG_HEADING,
    intro: str = BLOG_INTRO,
    include_disclosure: bool = True,
    open_marker: str = BLOG_BLOCK_OPEN,
    close_marker: str = BLOG_BLOCK_CLOSE,
) -> str:
    """Render the marker-wrapped picks block. `""` when `products` is empty.

    Args:
        products: Catalog entries to feature, in display order.
        slug: The post's WP slug -- becomes the `ascsubtag` campaign id.
        associates_tag: Amazon Associates tag. Required: rendering naked
            Amazon links violates the Associates T&C, so an empty tag is a
            `ValueError`, never a silently untagged link.
        heading: `<h2>` text. Override to keep an existing post's wording.
        intro: Lead-in line under the heading.
        include_disclosure: Append the FTC/Amazon disclosure paragraph.
            Pass `False` when the post already carries one elsewhere
            (see `lib.affiliate_resolver._has_disclosure`).
        open_marker / close_marker: Idempotency markers. Override to
            re-render inside another block's markers.

    Raises:
        ValueError: If `associates_tag` is empty.
    """
    if not products:
        return ""
    if not associates_tag:
        raise ValueError("associates_tag is required to render affiliate URLs")

    items: list[str] = []
    for product in products:
        url = _affiliate_url(product.asin, associates_tag, slug)
        detail = _detail_text(product)
        detail_html = f" — {_html.escape(detail)}" if detail else ""
        items.append(
            f"  <li><strong>{_html.escape(product.display)}</strong>{detail_html} "
            f'<a href="{_html.escape(url)}" rel="sponsored nofollow" '
            f'target="_blank">View on Amazon</a></li>'
        )
    items_html = "\n".join(items)

    disclosure_html = f"<p><em>{_html.escape(DISCLOSURE)}</em></p>\n" if include_disclosure else ""
    return (
        f"{open_marker}\n"
        f"<h2>{_html.escape(heading)}</h2>\n"
        f"<p>{_html.escape(intro)}</p>\n"
        f"<ul>\n{items_html}\n</ul>\n"
        f"{disclosure_html}"
        f"{close_marker}"
    )


def has_blog_block(html: str) -> bool:
    """True when `html` already carries a blog picks block."""
    return BLOG_BLOCK_OPEN in html and BLOG_BLOCK_CLOSE in html


def strip_blog_block(html: str) -> str:
    """`html` with any blog picks block removed.

    Used to answer "does the POST's OWN prose carry a disclosure?" -- asking
    that of the un-stripped body would say yes purely because a previous
    pass's block is sitting in it, so the next pass would drop the
    disclosure, the pass after that would add it back, and the block would
    never converge.
    """
    return _BLOCK_RE.sub("", html)


def insert_or_replace_blog_block(html: str, block_html: str) -> str:
    """Idempotent insert. Replaces an existing block in place; otherwise
    inserts before the first FAQ heading; otherwise appends at the end.

    The replacement is done through a lambda so a `\\g`-looking sequence in
    the rendered block can never be interpreted as a regex backreference.
    """
    if not block_html:
        return html

    if has_blog_block(html):
        return _BLOCK_RE.sub(lambda _match: block_html, html, count=1)

    faq_match = _FAQ_HEADING_RE.search(html)
    if faq_match:
        idx = faq_match.start()
        return f"{html[:idx]}{block_html}\n\n{html[idx:]}"

    return f"{html.rstrip()}\n\n{block_html}\n"
