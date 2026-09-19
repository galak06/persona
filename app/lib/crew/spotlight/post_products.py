"""Which catalog products a published post carries, and what it says about one.

CONTRACT FROZEN BY PHASE 0 -- signatures, return shapes and the behaviour each
docstring spells out are final. Phase 1B replaces the ``NotImplementedError``
bodies and changes nothing else. ``brand_token_re`` / ``display_name`` come
from the already-complete ``lib.crew.spotlight.product_terms``.

Pure functions over strings and ``ProductEntry``: no network, no database, no
brand directory. The CALLER fetches the post (``lib.crew.wp_source.fetch_post``)
and loads the pool (``lib.crew.products.pool.load_candidate_pool``); that is
what lets the products route degrade to ``post_scan="unavailable"`` when
WordPress is unreachable and still return the catalog.

Anchored on the affiliate LINK, not on product names in prose, for the reason
``lib.certification_claims`` documents: a ``/dp/<ASIN>`` link is an exact
identifier, while Title Case headings are indistinguishable from brand names.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final
from urllib.parse import urlsplit

from lib.affiliate_resolver import ProductEntry
from lib.crew.products.block import BLOG_BLOCK_CLOSE, BLOG_BLOCK_OPEN
from lib.crew.products.compliance import slug_for
from lib.crew.spotlight.product_terms import brand_token_re

#: ``lib.certification_claims._ASIN_RE`` / ``products.discovery._ASIN_RE`` made
#: case-tolerant, and widened to the ``/gp/product/`` spelling: the blog block
#: always renders ``/dp/``, but a hand-written link pasted into an older post
#: (or a link copied off Amazon's own share sheet) uses the legacy path, and
#: missing one would report a featured product as absent from its own article.
#: No trailing boundary, matching the two modules above -- an ASIN is exactly
#: ten characters, so ``/dp/B006W6YHHI/ref=sr_1_3`` is a match plus a suffix.
_ASIN_RE: Final[re.Pattern[str]] = re.compile(r"/(?:dp|gp/product)/([A-Za-z0-9]{10})")

#: The picks block as ``lib.crew.products.block`` renders it. Built from the
#: public markers rather than importing that module's private ``_BLOCK_RE`` so
#: a change to the block's internals cannot silently change what counts here.
#: An unterminated OPEN marker matches nothing and therefore falls back to the
#: whole post -- the safe direction, since the alternative is finding no
#: products at all in a post that plainly links some.
_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    re.escape(BLOG_BLOCK_OPEN) + r".*?" + re.escape(BLOG_BLOCK_CLOSE),
    re.DOTALL,
)

#: The article's framing: who it is for, what problem it opens with. Always
#: included, because a caption grounded only on a mid-article paragraph reads
#: like a fragment of someone else's argument.
_HEAD_CHARS: Final[int] = 1200

#: How much context either side of a brand-token mention travels with it. Wide
#: enough to carry the sentence before and after the claim -- the qualifiers
#: ("for dogs over 25 lb", "after two weeks") usually live there, and a caption
#: that drops them is the failure mode this whole module exists to avoid.
_WINDOW_CHARS: Final[int] = 600

#: Marks a cut in the excerpt so the writer does not read two distant
#: paragraphs as one continuous argument.
_JOINER: Final[str] = " ... "


def asins_in_html(html: str) -> list[str]:
    """Every Amazon ASIN the post links to: upper-cased, de-duplicated, in
    first-appearance order.

    Scope: when the HTML contains a picks block (``block.BLOG_BLOCK_OPEN`` ...
    ``block.BLOG_BLOCK_CLOSE``) only links INSIDE the block(s) count -- those
    are the products the post deliberately features, and a passing mention
    elsewhere is not an endorsement to spotlight. When there is no block, every
    ``/dp/<ASIN>`` link in the post counts.

    ASIN pattern: ``/dp/([A-Za-z0-9]{10})`` (the repo's ``_ASIN_RE``, made
    case-tolerant). Empty / falsy input -> ``[]``.
    """
    if not html:
        return []
    blocks = _BLOCK_RE.findall(html)
    # The join is only a separator: matches never straddle two blocks because
    # an ASIN cannot contain a newline.
    haystack = "\n".join(blocks) if blocks else html
    # `dict.fromkeys` is the ordered-set idiom: first appearance wins, later
    # duplicates (the same product linked from the intro AND the block) drop.
    return list(dict.fromkeys(m.group(1).upper() for m in _ASIN_RE.finditer(haystack)))


def products_in_post(
    html: str, pool: Mapping[str, ProductEntry]
) -> tuple[list[ProductEntry], list[str]]:
    """``(in_post, unknown_asins)`` for one post against the merged pool.

    ``in_post``: the pool entries whose ASIN appears in ``asins_in_html(html)``,
    in the post's link order, one entry per ASIN (when two pool keys share an
    ASIN the FIRST in pool iteration order wins -- ``load_candidate_pool`` is
    curated-first, so that is the curated entry). ASIN comparison is
    case-insensitive and ignores surrounding whitespace.

    ``unknown_asins``: linked ASINs no pool entry carries, in link order. The
    route surfaces them so the owner can see a product the post links but the
    catalog has lost.
    """
    by_asin: dict[str, ProductEntry] = {}
    for entry in pool.values():
        asin = (entry.asin or "").strip().upper()
        # `setdefault` semantics on purpose: curated entries come first out of
        # `load_candidate_pool`, so the first key holding an ASIN is the one
        # with the hand-written note the prompt will quote.
        if asin and asin not in by_asin:
            by_asin[asin] = entry

    in_post: list[ProductEntry] = []
    unknown: list[str] = []
    for asin in asins_in_html(html):
        linked = by_asin.get(asin)
        if linked is None:
            unknown.append(asin)
        else:
            in_post.append(linked)
    return in_post, unknown


def product_excerpt(body_text: str, product: ProductEntry, *, max_chars: int = 3000) -> str:
    """The part of the article worth grounding a caption about ``product`` on.

    ``body_text`` is already plain text (``wp_source.strip_html``). The regular
    social post sends the first 3000 characters; a product is often discussed
    2000 words in, so that window would ground a spotlight on an introduction
    that never mentions it.

    * Brand token found (``product_terms.brand_token_re(product).finditer``):
      the first 1200 characters (the article's framing) plus a window of 600
      characters either side of each match, in document order, overlapping
      windows merged, pieces joined with ``" ... "``, stopping before the
      result would exceed ``max_chars``.
    * No token, or no match (the product is not in the post): the first
      ``max_chars`` characters -- the same grounding a regular post gets.

    Never longer than ``max_chars``; ``""`` for empty input.
    """
    if not body_text:
        return ""
    pattern = brand_token_re(product)
    matches = list(pattern.finditer(body_text)) if pattern else []
    if not matches:
        return body_text[:max_chars]

    spans = [(0, min(_HEAD_CHARS, len(body_text)))]
    spans.extend(
        (max(0, m.start() - _WINDOW_CHARS), min(len(body_text), m.end() + _WINDOW_CHARS))
        for m in matches
    )

    # Merge before truncating. Two mentions a paragraph apart produce windows
    # that overlap heavily; emitting them separately would repeat the shared
    # text twice and burn the budget on a duplicate.
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    pieces: list[str] = []
    used = 0
    for start, end in merged:
        piece = body_text[start:end]
        cost = len(piece) + (len(_JOINER) if pieces else 0)
        # Stop, never truncate mid-window: a window cut in half can end in the
        # middle of the very claim it was selected to carry.
        if used + cost > max_chars:
            break
        pieces.append(piece)
        used += cost
    if not pieces:
        # The head alone (or the head merged with an early window) is already
        # over budget. Fall back rather than return "" -- an empty excerpt
        # grounds the caption on nothing at all.
        return body_text[:max_chars]
    return _JOINER.join(pieces)


def post_slug(post: Mapping[str, Any] | None, *, wp_url: str = "", fallback_title: str = "") -> str:
    """The post slug the ``fb-``/``ig-`` campaign ids are built from. Never
    empty.

    One definition because two callers must agree: the products route reports
    it and ``compose_spotlight`` bakes it into the stored affiliate URLs.
    Order: (1) ``post["slug"]`` when ``post`` is given and the field is
    non-blank -- the same value the blog's own ``blog-<slug>`` links carry for a
    PUBLISHED post (``blog_backfill.WpPost.campaign_slug``); (2) the last
    non-empty path segment of ``wp_url``, for when WordPress was unreachable;
    (3) ``lib.crew.products.compliance.slug_for(fallback_title)``, which itself
    bottoms out at ``"post"``.
    """
    if post is not None:
        slug = str(post.get("slug") or "").strip()
        if slug:
            return slug
    # `urlsplit` rather than a `/`-split of the whole URL: the latter returns
    # the HOST for a bare `https://dogfoodandfun.com`, and a `?p=<id>` plain
    # permalink would hand back the query string as if it were a slug.
    for segment in reversed(urlsplit(wp_url or "").path.split("/")):
        cleaned = segment.strip()
        if cleaned:
            return cleaned
    return slug_for(fallback_title)
