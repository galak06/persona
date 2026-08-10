"""Tests for lib.crew.products.block -- the blog affiliate picks renderer.

Pure string work: no network, no DeepSeek, no DB, no BRAND_DIR. The two
things worth being paranoid about are (a) the block is idempotent, because
the backfill re-runs over live posts, and (b) nothing user-visible escapes
`html.escape` or leaks a price (Amazon Associates forbids stated prices
that aren't pulled live from their API).
"""
# ruff: noqa: S101

from __future__ import annotations

import pytest

from lib.affiliate_resolver import ProductEntry, _has_disclosure
from lib.crew.products.block import (
    BLOG_BLOCK_CLOSE,
    BLOG_BLOCK_OPEN,
    BLOG_HEADING,
    has_blog_block,
    insert_or_replace_blog_block,
    render_blog_block,
)

_TAG = "dogfoodandfun-20"

_GPS = ProductEntry(
    key="gps-tracker",
    asin="B000000001",
    display="PawTrack GPS Collar",
    category="gps-tracker",
    notes="no-subscription tracker",
)
_BARE = ProductEntry(key="bare", asin="B000000002", display="Plain Bowl")
_CATEGORY_ONLY = ProductEntry(
    key="cat-only", asin="B000000003", display="Cooling Vest", category="cooling-gear"
)
_SPICY = ProductEntry(
    key="spicy",
    asin="B000000004",
    display='Chew & "Tug" <Toy>',
    notes="holds up to a 60 lb dog & a hose",
)


class TestRenderBlogBlock:
    def test_no_products_renders_nothing(self):
        assert render_blog_block([], "some-slug", associates_tag=_TAG) == ""

    def test_empty_tag_is_refused(self):
        with pytest.raises(ValueError, match="associates_tag"):
            render_blog_block([_GPS], "some-slug", associates_tag="")

    def test_link_carries_tag_subtag_and_sponsored_rel(self):
        block = render_blog_block([_GPS], "dog-gps-guide", associates_tag=_TAG)
        assert "https://www.amazon.com/dp/B000000001?tag=dogfoodandfun-20" in block
        assert "ascsubtag=blog-dog-gps-guide" in block
        assert 'rel="sponsored nofollow"' in block
        assert 'target="_blank"' in block
        assert block.startswith(BLOG_BLOCK_OPEN)
        assert block.endswith(BLOG_BLOCK_CLOSE)

    def test_default_wording_is_blog_not_recipe(self):
        block = render_blog_block([_GPS], "dog-gps-guide", associates_tag=_TAG)
        assert f"<h2>{BLOG_HEADING}</h2>" in block
        assert "Recipe" not in block

    def test_text_is_html_escaped(self):
        block = render_blog_block([_SPICY], "chew-toys", associates_tag=_TAG)
        assert "<Toy>" not in block
        assert "&lt;Toy&gt;" in block
        assert "&quot;Tug&quot;" in block
        # The URL's own separator is escaped too, so the href stays valid HTML.
        assert "&amp;ascsubtag=" in block

    def test_missing_notes_and_category_coalesce_to_nothing(self):
        block = render_blog_block([_BARE], "bowls", associates_tag=_TAG)
        assert "None" not in block
        # No dangling em-dash separator when there is no detail text.
        assert "<strong>Plain Bowl</strong> <a href=" in block

    def test_category_is_the_fallback_detail(self):
        block = render_blog_block([_CATEGORY_ONLY], "summer", associates_tag=_TAG)
        assert "<strong>Cooling Vest</strong> — cooling-gear <a href=" in block

    def test_never_renders_a_price(self):
        block = render_blog_block([_GPS, _BARE], "dog-gps-guide", associates_tag=_TAG)
        lowered = block.lower()
        assert "$" not in block
        assert "price" not in lowered
        assert "usd" not in lowered

    def test_disclosure_included_by_default(self):
        block = render_blog_block([_GPS], "dog-gps-guide", associates_tag=_TAG)
        assert "As an Amazon Associate" in block
        assert _has_disclosure(block) is True

    def test_disclosure_can_be_suppressed(self):
        block = render_blog_block(
            [_GPS], "dog-gps-guide", associates_tag=_TAG, include_disclosure=False
        )
        assert "As an Amazon Associate" not in block
        assert "<ul>" in block

    def test_custom_heading_intro_and_markers(self):
        block = render_blog_block(
            [_GPS],
            "pumpkin-treats",
            associates_tag=_TAG,
            heading="Our Pick: Tools Used in This Recipe",
            intro="What I actually use at home when cooking for Nalla:",
            open_marker="<!-- recipe-tools-block:v1 -->",
            close_marker="<!-- /recipe-tools-block -->",
        )
        assert block.startswith("<!-- recipe-tools-block:v1 -->")
        assert block.endswith("<!-- /recipe-tools-block -->")
        assert BLOG_BLOCK_OPEN not in block
        assert "<h2>Our Pick: Tools Used in This Recipe</h2>" in block


class TestInsertOrReplace:
    _BODY_NO_FAQ = "<p>Intro paragraph.</p>\n<h2>Why it matters</h2>\n<p>Because.</p>"
    _BODY_WITH_FAQ = (
        "<p>Intro paragraph.</p>\n"
        "<h2>Why it matters</h2>\n<p>Because.</p>\n"
        '<h2 class="wp-block-heading">FAQ</h2>\n<h3>Is it safe?</h3>'
    )

    def _block(self, product: ProductEntry = _GPS) -> str:
        return render_blog_block([product], "dog-gps-guide", associates_tag=_TAG)

    def test_has_blog_block_detects_only_a_real_block(self):
        assert has_blog_block(self._BODY_NO_FAQ) is False
        assert has_blog_block(insert_or_replace_blog_block(self._BODY_NO_FAQ, self._block()))

    def test_empty_block_leaves_html_untouched(self):
        assert insert_or_replace_blog_block(self._BODY_NO_FAQ, "") == self._BODY_NO_FAQ

    def test_appends_when_there_is_no_faq(self):
        out = insert_or_replace_blog_block(self._BODY_NO_FAQ, self._block())
        assert out.index("Because.") < out.index(BLOG_BLOCK_OPEN)

    def test_inserts_before_the_first_faq_heading(self):
        out = insert_or_replace_blog_block(self._BODY_WITH_FAQ, self._block())
        assert out.index(BLOG_BLOCK_OPEN) < out.index(">FAQ<")
        assert out.index(BLOG_BLOCK_CLOSE) < out.index(">FAQ<")

    def test_second_pass_replaces_and_never_duplicates(self):
        once = insert_or_replace_blog_block(self._BODY_WITH_FAQ, self._block())
        twice = insert_or_replace_blog_block(once, self._block(_CATEGORY_ONLY))
        assert twice.count(BLOG_BLOCK_OPEN) == 1
        assert twice.count(BLOG_BLOCK_CLOSE) == 1
        assert "PawTrack GPS Collar" not in twice
        assert "Cooling Vest" in twice
        # The surrounding post body survives the swap intact.
        assert ">FAQ<" in twice
        assert "Because." in twice

    def test_replacement_is_literal_not_a_regex_template(self):
        # `\1` survives html.escape untouched, and would blow up as an
        # invalid group reference if the block were used as an re.sub template.
        tricky = ProductEntry(key="tricky", asin="B000000009", display=r"Tuff \1 Toy")
        once = insert_or_replace_blog_block(self._BODY_NO_FAQ, self._block())
        twice = insert_or_replace_blog_block(once, self._block(tricky))
        assert r"Tuff \1 Toy" in twice
        assert twice.count(BLOG_BLOCK_OPEN) == 1
