"""Affiliate links for SOCIAL placements of a catalog product.

CONTRACT FROZEN BY PHASE 0 -- signatures, the URL shape and the error contract
below are final. Phase 1B replaces the ``NotImplementedError`` bodies and
changes nothing else.

The blog's links carry ``ascsubtag=blog-<slug>``
(``lib.crew.products.block.blog_campaign_id``), which is what lets Amazon's
reports separate a post's own clicks from the recipe block's ``recipe-`` ones.
A product spotlight sends the same product to the same post's audience through
two more doors -- a Facebook DM and an Instagram DM -- and without its own tag
those clicks would be indistinguishable from the blog's. ``fb-<slug>`` /
``ig-<slug>`` keep the post slug as the shared suffix on purpose: one post's
three placements line up under one slug in a report.

These links never appear in a caption (both platforms suppress caption links;
``lib.crew.socialpost.rules`` blocks them). They are stored on the
``content_derivatives`` row and shown copyable on the review card, because the
owner fulfils the "Comment KEYWORD" call-to-action by hand.

Deliberately NOT added to ``lib.crew.products.__init__``'s re-exports: that
package ``__init__`` imports the selector and the writer stack, and this module
is imported by the compose path only. Import it by its full path.
"""

from __future__ import annotations

from typing import Final, Literal

from lib.affiliate_resolver import build_affiliate_url

#: Which DM door the link is handed out through.
Channel = Literal["fb", "ig"]

CHANNELS: Final[tuple[str, ...]] = ("fb", "ig")


def social_campaign_id(channel: Channel, slug: str) -> str:
    """The ``ascsubtag`` for a social placement on ``slug``: ``"fb-<slug>"`` or
    ``"ig-<slug>"``.

    The one definition of that id, the way ``block.blog_campaign_id`` is for
    ``blog-``. ``slug`` is used as given (it is already a WP slug --
    ``lib.crew.spotlight.post_products.post_slug`` produces it).

    Raises ``ValueError`` when ``channel`` is not in ``CHANNELS`` or
    ``slug.strip()`` is empty -- a dead ``ascsubtag=fb-`` has already shipped
    once on the blog side (see ``compliance.slug_for``) and attributes nothing.
    """
    # `channel` is typed as a Literal, but the value reaching here comes from a
    # caller's string (a worker's `--platform`, a DB column) that mypy never
    # saw, so the check is a runtime one. Raising beats silently minting an
    # `ascsubtag=facebook-<slug>` that no report groups with the other two.
    if channel not in CHANNELS:
        raise ValueError(f"unknown social channel: {channel!r} — expected one of {CHANNELS}")
    if not slug.strip():
        raise ValueError("slug is empty — a bare 'fb-'/'ig-' ascsubtag attributes nothing")
    # Used as given, NOT re-slugified: `post_slug` already produced a WP slug,
    # and normalising it a second time here could quietly disagree with the
    # `blog-<slug>` the same post's own links carry.
    return f"{channel}-{slug}"


def spotlight_affiliate_url(asin: str, tag: str, *, channel: Channel, slug: str) -> str:
    """``https://www.amazon.com/dp/<asin>?tag=<tag>&ascsubtag=<fb|ig>-<slug>``.

    Byte-for-byte the shape of ``block._affiliate_url`` with the campaign id
    swapped. Build it with the PUBLIC
    ``lib.affiliate_resolver.build_affiliate_url(asin, tag,
    social_campaign_id(channel, slug))`` rather than re-spelling the format
    string or importing the private helper. A raw ``&`` (not ``&amp;``): this
    URL is pasted into a DM, never rendered into HTML.

    Raises ``ValueError`` when ``tag.strip()`` or ``asin.strip()`` is empty
    (an untagged Amazon link earns nothing and, handed out under an affiliate
    disclosure, is simply wrong), plus everything ``social_campaign_id``
    raises. ``asin`` and ``tag`` are stripped before use.
    """
    clean_asin = asin.strip()
    if not clean_asin:
        raise ValueError("asin is empty — cannot build a product link without one")
    clean_tag = tag.strip()
    if not clean_tag:
        # The blog path fails the same way (`affiliate_resolver.resolve_html`
        # raises on a missing AMAZON_ASSOCIATES_TAG) and for the same reason:
        # an untagged link earns nothing, and handing one out under an
        # affiliate disclosure states a commercial relationship that the link
        # itself does not carry.
        raise ValueError("associates tag is empty — refusing to build an untagged Amazon link")
    return build_affiliate_url(clean_asin, clean_tag, social_campaign_id(channel, slug))
