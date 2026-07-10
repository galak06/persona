"""Render the "Our Pick: Tools Used in This Recipe" block + idempotent insert.

Produces self-contained HTML that includes:
    - <h2> heading
    - intro line
    - <ul> with affiliate-tagged Amazon links
    - FTC disclosure (Amazon Associates requires it)

Idempotency: every block is wrapped in BLOCK_MARKER_OPEN / BLOCK_MARKER_CLOSE
HTML comments. insert_or_replace_block() detects the markers and replaces
in-place, so re-running the backfill never duplicates.
"""

from __future__ import annotations

import html as _html
import re
from typing import Final

from .catalog import RecipeProduct

BLOCK_MARKER_OPEN: Final[str] = "<!-- recipe-tools-block:v1 -->"
BLOCK_MARKER_CLOSE: Final[str] = "<!-- /recipe-tools-block -->"

_DISCLOSURE: Final[str] = (
    "As an Amazon Associate, I earn from qualifying purchases. "
    "We only recommend gear we actually use."
)

_INTRO: Final[str] = "What I actually use at home when cooking for Nalla:"

# Match the block whether or not it has surrounding whitespace.
_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    re.escape(BLOCK_MARKER_OPEN) + r".*?" + re.escape(BLOCK_MARKER_CLOSE),
    re.DOTALL,
)

# FAQ heading — case-insensitive, matches "FAQ", "FAQs", "Frequently Asked Questions".
_FAQ_HEADING_RE: Final[re.Pattern[str]] = re.compile(
    r"<h2\b[^>]*>\s*(?:FAQ[s]?|Frequently\s+Asked\s+Questions?)\b[^<]*</h2>",
    re.IGNORECASE,
)


def _affiliate_url(asin: str, tag: str, slug: str) -> str:
    return f"https://www.amazon.com/dp/{asin}?tag={tag}&ascsubtag=recipe-{slug}"


def render_block(
    products: list[RecipeProduct],
    slug: str,
    *,
    associates_tag: str,
) -> str:
    """Render the HTML block for a list of products. Returns empty string if no products."""
    if not products:
        return ""
    if not associates_tag:
        raise ValueError("associates_tag is required to render affiliate URLs")

    items: list[str] = []
    for p in products:
        url = _affiliate_url(p.asin, associates_tag, slug)
        items.append(
            f'  <li><strong>{_html.escape(p.display)}</strong> — '
            f'{_html.escape(p.blurb)} '
            f'<a href="{_html.escape(url)}" rel="sponsored nofollow" '
            f'target="_blank">View on Amazon</a></li>'
        )
    items_html = "\n".join(items)

    return (
        f"{BLOCK_MARKER_OPEN}\n"
        f'<h2>Our Pick: Tools Used in This Recipe</h2>\n'
        f"<p>{_INTRO}</p>\n"
        f"<ul>\n{items_html}\n</ul>\n"
        f"<p><em>{_DISCLOSURE}</em></p>\n"
        f"{BLOCK_MARKER_CLOSE}"
    )


def has_block(html: str) -> bool:
    return BLOCK_MARKER_OPEN in html and BLOCK_MARKER_CLOSE in html


def insert_or_replace_block(html: str, block_html: str) -> str:
    """Idempotent insert. If the block already exists, replace it; otherwise
    insert before the first FAQ heading; otherwise append at the end.
    """
    if not block_html:
        return html

    if has_block(html):
        return _BLOCK_RE.sub(block_html, html, count=1)

    faq_match = _FAQ_HEADING_RE.search(html)
    if faq_match:
        idx = faq_match.start()
        return f"{html[:idx]}{block_html}\n\n{html[idx:]}"

    return f"{html.rstrip()}\n\n{block_html}\n"
