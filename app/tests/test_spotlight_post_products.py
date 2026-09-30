"""`lib.crew.spotlight.post_products` -- what a published post actually links
to, and what it says about one of those products.

Three behaviours here are load-bearing for the spotlight, and each is asserted
for the reason it exists rather than for coverage:

* Scope. A post with a picks block has DECIDED which products it features; an
  ASIN mentioned in a sentence somewhere above is not an endorsement. Without
  the block the whole post is fair game, because there is nothing narrower.
* Excerpt. The regular social post grounds on the first 3000 characters. A
  product is routinely discussed 2000 words in, so that window would ground a
  spotlight caption on an introduction that never names the product -- which is
  precisely how a caption invents a claim.
* Slug. Two callers (the products route, `compose_spotlight`) must agree on it,
  because it is baked into stored affiliate URLs that outlive the row.

Pure functions -- no fakes, no I/O.
"""

from __future__ import annotations

import pytest

from lib.affiliate_resolver import ProductEntry
from lib.crew.products.block import BLOG_BLOCK_CLOSE, BLOG_BLOCK_OPEN
from lib.crew.spotlight.post_products import (
    asins_in_html,
    post_slug,
    product_excerpt,
    products_in_post,
)

# The real catalog entry, copied verbatim: the excerpt and the "certification
# verified" badge are both driven off this exact `display`/`notes` shape.
GREENIES = ProductEntry(
    key="greenies-dental-treats",
    asin="B006W6YHHI",
    display="Greenies Regular Dental Dog Treats (original chicken, 36 ct)",
    category="dental-care",
    notes="Daily dental chew. RE-VERIFIED against vohc.org on 2026-08-02.",
)
FI_COLLAR = ProductEntry(
    key="fi-series-3",
    asin="B08LMN4321",
    display="Fi Series 3+ Smart Dog Collar",
    category="gps-tracking",
    notes="GPS tracker; battery holds three weeks on Nalla.",
)
POOL = {GREENIES.key: GREENIES, FI_COLLAR.key: FI_COLLAR}

_ORPHAN_ASIN = "B0CQ9Z1111"


def _link(asin: str, *, path: str = "dp") -> str:
    return f'<p><a href="https://www.amazon.com/{path}/{asin}?tag=dogfoodandfun-20">buy</a></p>'


def _block(*asins: str) -> str:
    return BLOG_BLOCK_OPEN + "".join(_link(a) for a in asins) + BLOG_BLOCK_CLOSE


# --------------------------------------------------------------------------
# asins_in_html
# --------------------------------------------------------------------------


def test_without_a_picks_block_every_dp_link_in_the_post_counts() -> None:
    html = _link(FI_COLLAR.asin) + "<h2>Chews</h2>" + _link(GREENIES.asin)
    assert asins_in_html(html) == [FI_COLLAR.asin, GREENIES.asin]


def test_a_picks_block_narrows_the_scan_to_its_own_links() -> None:
    html = "<p>intro</p>" + _link(FI_COLLAR.asin) + _block(GREENIES.asin) + _link(_ORPHAN_ASIN)
    # The Fi link in the prose and the orphan below the block are both ignored:
    # the block is the post's deliberate selection.
    assert asins_in_html(html) == [GREENIES.asin]


def test_two_picks_blocks_are_both_in_scope() -> None:
    html = _block(GREENIES.asin) + "<p>more</p>" + _block(FI_COLLAR.asin)
    assert asins_in_html(html) == [GREENIES.asin, FI_COLLAR.asin]


def test_an_unterminated_open_marker_falls_back_to_the_whole_post() -> None:
    # Safe direction: a truncated block must not make a post that plainly
    # links products look like it links none.
    html = "<p>x</p>" + _link(FI_COLLAR.asin) + BLOG_BLOCK_OPEN + _link(GREENIES.asin)
    assert asins_in_html(html) == [FI_COLLAR.asin, GREENIES.asin]


def test_legacy_gp_product_links_are_found_too() -> None:
    html = _link(GREENIES.asin, path="gp/product") + _link(FI_COLLAR.asin)
    assert asins_in_html(html) == [GREENIES.asin, FI_COLLAR.asin]


def test_asins_are_upper_cased_and_de_duplicated_in_first_appearance_order() -> None:
    html = (
        _link(FI_COLLAR.asin)
        + _link(GREENIES.asin.lower())
        + _link(GREENIES.asin)
        + _link(FI_COLLAR.asin, path="gp/product")
    )
    assert asins_in_html(html) == [FI_COLLAR.asin, GREENIES.asin]


@pytest.mark.parametrize("html", ["", "<p>no links at all</p>", _block()])
def test_no_links_yields_an_empty_list(html: str) -> None:
    assert asins_in_html(html) == []


# --------------------------------------------------------------------------
# products_in_post
# --------------------------------------------------------------------------


def test_products_in_post_resolves_pool_entries_in_link_order() -> None:
    html = _link(FI_COLLAR.asin) + _link(GREENIES.asin)
    found, unknown = products_in_post(html, POOL)
    assert found == [FI_COLLAR, GREENIES]
    assert unknown == []


def test_products_in_post_reports_linked_asins_the_catalog_has_lost() -> None:
    html = _link(_ORPHAN_ASIN) + _link(GREENIES.asin)
    found, unknown = products_in_post(html, POOL)
    assert found == [GREENIES]
    assert unknown == [_ORPHAN_ASIN]


def test_a_duplicate_asin_yields_one_entry() -> None:
    html = _link(GREENIES.asin) + _link(GREENIES.asin.lower())
    found, unknown = products_in_post(html, POOL)
    assert found == [GREENIES]
    assert unknown == []


def test_when_two_pool_keys_share_an_asin_the_first_wins() -> None:
    # `load_candidate_pool` is curated-first, so the first key holding an ASIN
    # is the entry with the hand-written note the prompt will quote.
    stale = ProductEntry(key="greenies-old", asin=GREENIES.asin.lower(), display="Greenies")
    curated_first = {GREENIES.key: GREENIES, stale.key: stale}
    discovered_first = {stale.key: stale, GREENIES.key: GREENIES}
    assert products_in_post(_link(GREENIES.asin), curated_first)[0] == [GREENIES]
    assert products_in_post(_link(GREENIES.asin), discovered_first)[0] == [stale]


def test_an_empty_pool_makes_every_linked_asin_unknown() -> None:
    found, unknown = products_in_post(_link(GREENIES.asin), {})
    assert found == []
    assert unknown == [GREENIES.asin]


# --------------------------------------------------------------------------
# product_excerpt
# --------------------------------------------------------------------------


#: The shape that motivates the whole function: the product is first named at
#: character 3500, past the 3000 a regular social post would have read. The
#: surrounding spaces are not decoration -- `brand_token_re` is whole-word.
LATE_MENTION_BODY = "H" * 3499 + " Greenies " + "T" * 1000


def test_excerpt_keeps_the_framing_and_a_window_around_a_late_mention() -> None:
    body = LATE_MENTION_BODY
    assert "Greenies" not in body[:3000]  # the window a regular post would read

    excerpt = product_excerpt(body, GREENIES)
    # Head (0, 1200) plus the match's window (2900, 4108), joined by a cut mark.
    assert excerpt == body[:1200] + " ... " + body[2900:4108]
    assert "Greenies" in excerpt
    assert len(excerpt) <= 3000


def test_a_mention_close_to_the_framing_merges_into_one_continuous_piece() -> None:
    body = "H" * 1299 + " Greenies " + "T" * 2000
    excerpt = product_excerpt(body, GREENIES)
    # Window (700, 1908) overlaps the head (0, 1200): one piece, no cut mark.
    assert excerpt == body[:1908]
    assert " ... " not in excerpt


def test_two_nearby_mentions_share_one_merged_window() -> None:
    body = "H" * 1999 + " Greenies " + "M" * 190 + " Greenies " + "T" * 2000
    excerpt = product_excerpt(body, GREENIES)
    # Windows (1400, 2608) and (1600, 2808) merge, so the text they share is
    # emitted once; the head stays separate.
    assert excerpt == body[:1200] + " ... " + body[1400:2808]
    assert excerpt.count(" ... ") == 1


def test_a_window_that_does_not_fit_the_budget_is_dropped_whole() -> None:
    excerpt = product_excerpt(LATE_MENTION_BODY, GREENIES, max_chars=1300)
    # Stopped rather than truncated: half a window can end mid-claim.
    assert excerpt == "H" * 1200
    assert len(excerpt) <= 1300


def test_a_budget_smaller_than_the_framing_falls_back_to_a_plain_prefix() -> None:
    excerpt = product_excerpt(LATE_MENTION_BODY, GREENIES, max_chars=100)
    # Never "": an empty excerpt grounds the caption on nothing.
    assert excerpt == LATE_MENTION_BODY[:100]


def test_a_product_the_post_never_mentions_gets_the_ordinary_prefix() -> None:
    # This is the case the owner reaches by spotlighting a catalog product the
    # article does not carry -- allowed on purpose, so it must not return "".
    body = "H" * 5000
    assert product_excerpt(body, GREENIES) == body[:3000]
    assert product_excerpt(body, GREENIES, max_chars=500) == body[:500]


def test_a_product_with_no_brand_token_gets_the_ordinary_prefix() -> None:
    nameless = ProductEntry(key="", asin="B000000000", display="")
    body = "Greenies everywhere. " * 300
    assert product_excerpt(body, nameless) == body[:3000]


def test_empty_body_text_yields_an_empty_excerpt() -> None:
    assert product_excerpt("", GREENIES) == ""


# --------------------------------------------------------------------------
# post_slug
# --------------------------------------------------------------------------


def test_post_slug_prefers_the_wordpress_slug() -> None:
    post = {"slug": "best-dental-chews", "link": "https://dogfoodandfun.com/other/"}
    assert post_slug(post, wp_url="https://dogfoodandfun.com/other/", fallback_title="T") == (
        "best-dental-chews"
    )


@pytest.mark.parametrize(
    "wp_url",
    [
        "https://dogfoodandfun.com/best-dental-chews/",
        "https://dogfoodandfun.com/best-dental-chews",
        "https://dogfoodandfun.com/blog/best-dental-chews/?utm_source=ig",
        "https://dogfoodandfun.com/best-dental-chews/#recipe",
    ],
)
def test_a_blank_wordpress_slug_falls_through_to_the_url_path(wp_url: str) -> None:
    # WP returns `slug: ""` for a draft; the URL is the next-best source.
    assert post_slug({"slug": "  "}, wp_url=wp_url) == "best-dental-chews"
    assert post_slug(None, wp_url=wp_url) == "best-dental-chews"


@pytest.mark.parametrize("wp_url", ["", "https://dogfoodandfun.com", "https://x.com/?p=4289"])
def test_a_url_with_no_path_segment_falls_through_to_the_title(wp_url: str) -> None:
    # The host is not a slug, and neither is a plain `?p=<id>` permalink.
    assert post_slug(None, wp_url=wp_url, fallback_title="Best Dental Chews!") == (
        "best-dental-chews"
    )


def test_post_slug_is_never_empty() -> None:
    assert post_slug(None) == "post"
    assert post_slug({}, wp_url="", fallback_title="") == "post"
    assert post_slug({"slug": None}, wp_url="///", fallback_title="   ") == "post"
