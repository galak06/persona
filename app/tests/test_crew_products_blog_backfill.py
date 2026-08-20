"""Tests for the backfill-side product modules (no WordPress, no network).

Covers `lib.crew.products.brief_from_post` (rebuilding a `ContentBrief`
from live post HTML), `lib.crew.products.blog_backfill`'s guards/renderers,
and `lib.crew.products.blog_selection`'s deliberately fallback-free
selection. The end-to-end CLI run is tested separately in
tests/test_backfill_blog_product_blocks.py.

The selector is exercised through the same `SelectorExecuteFn` seam
tests/test_crew_products_select.py uses -- no CrewAI kickoff, no DeepSeek.
"""
# ruff: noqa: S101

from __future__ import annotations

from typing import Any

import pytest

from lib.affiliate_resolver import ProductEntry
from lib.crew.products.blog_backfill import (
    MODE_REFRESH_RECIPE,
    WpPost,
    is_elementor,
    mode_for,
    prose_without_blocks,
    render_for_mode,
    to_post,
)
from lib.crew.products.blog_selection import select_products_for_existing_post
from lib.crew.products.brief_from_post import synthesize_brief
from lib.crew.products.models import ProductSelection, SelectedProduct
from lib.recipe_products import block_renderer as recipe_block

_BODY = (
    "<p>Nalla lost her collar on a trail, so I tested trackers.</p>\n"
    "<h2>How GPS trackers work</h2>\n"
    "<p>Cellular trackers ping a tower every few minutes &amp; drain battery.</p>\n"
    '<h2 class="wp-block-heading">FAQ</h2>\n<h3>Do they need a subscription?</h3>'
)

_POOL = {
    "gps-tracker": ProductEntry(
        key="gps-tracker",
        asin="B000000001",
        display="PawTrack GPS Collar",
        category="gps-tracker",
        notes="no-subscription tracker",
    ),
    "run-harness": ProductEntry(
        key="run-harness", asin="B000000002", display="TrailBlazer Harness", category="gear"
    ),
    "cooling-vest": ProductEntry(
        key="cooling-vest", asin="B000000003", display="ChillPup Cooling Vest"
    ),
}


def _fake_execute(*keys: str, seen: list[str] | None = None):
    """A `SelectorExecuteFn` returning a canned pick, recording each prompt."""

    def _execute(_agent: Any, task: Any) -> ProductSelection:
        if seen is not None:
            seen.append(task.description)
        return ProductSelection(
            products=[SelectedProduct(key=key, reason="fits this post") for key in keys]
        )

    return _execute


def _brief(slug: str = "dog-gps-guide", body: str = _BODY):
    return synthesize_brief(title="Best Dog GPS Trackers", slug=slug, content_html=body)


class TestSyntheticBrief:
    def test_brief_from_realistic_wp_html(self):
        brief = synthesize_brief(
            title="Best Dog GPS Trackers &#8212; 2026", slug="dog-gps-guide", content_html=_BODY
        )
        assert brief.suggested_title == "Best Dog GPS Trackers — 2026"
        assert brief.primary_keyword == "dog gps guide"
        assert [section.heading for section in brief.outline] == ["How GPS trackers work", "FAQ"]
        assert brief.outline[0].notes.startswith("Cellular trackers ping a tower")
        assert "&amp;" not in brief.outline[0].notes
        assert brief.mascot_angle  # required field, honest placeholder

    def test_headingless_post_still_yields_a_valid_brief(self):
        brief = synthesize_brief(title="", slug="no-headings-here", content_html="<p>Hi.</p>")
        assert brief.outline == []
        assert brief.suggested_title == "no headings here"
        assert brief.primary_keyword == "no headings here"

    def test_heading_with_no_body_text_still_gets_notes(self):
        brief = synthesize_brief(title="T", slug="s", content_html="<h2>Bare</h2>")
        assert brief.outline[0].heading == "Bare"
        assert brief.outline[0].notes

    def test_notes_are_truncated_not_unbounded(self):
        body = "<h2>Long</h2><p>" + ("word " * 200) + "</p>"
        brief = synthesize_brief(title="T", slug="s", content_html=body)
        assert len(brief.outline[0].notes) <= 205
        assert brief.outline[0].notes.endswith("...")


class TestPostParsing:
    def test_edit_context_row_maps_to_raw_content(self):
        post = to_post(
            {
                "id": "101",
                "slug": "dog-gps-guide",
                "title": {"raw": "Raw Title", "rendered": "Rendered"},
                "content": {"raw": _BODY, "rendered": "<p>rendered</p>"},
                "meta": {"_elementor_data": ""},
            }
        )
        assert post == WpPost(
            id=101,
            slug="dog-gps-guide",
            title="Raw Title",
            content=_BODY,
            meta={"_elementor_data": ""},
        )


class TestGuards:
    @pytest.mark.parametrize(
        "meta",
        [
            {"_elementor_data": '[{"id":"a"}]'},
            {"_elementor_data": ["something"]},
            {"_elementor_edit_mode": "builder"},
        ],
    )
    def test_elementor_meta_is_detected(self, meta: dict[str, Any]):
        assert is_elementor(meta) is True

    @pytest.mark.parametrize(
        "meta",
        [{}, {"_elementor_data": "  ", "_elementor_edit_mode": ""}, {"_elementor_data": []}],
    )
    def test_cleared_or_absent_elementor_meta_is_not_elementor(self, meta: dict[str, Any]):
        assert is_elementor(meta) is False

    def test_mode_detection(self):
        assert mode_for("<p>plain</p>", include_recipe_blocks=False) == "insert"
        blog = "<p>x</p><!-- blog-picks-block:v1 -->x<!-- /blog-picks-block -->"
        assert mode_for(blog, include_recipe_blocks=False) == "refresh-blog"
        recipe = f"{recipe_block.BLOCK_MARKER_OPEN}old{recipe_block.BLOCK_MARKER_CLOSE}"
        assert mode_for(recipe, include_recipe_blocks=False) == "skip-has-recipe-block"
        assert mode_for(recipe, include_recipe_blocks=True) == MODE_REFRESH_RECIPE

    def test_prose_without_blocks_drops_both_block_kinds(self):
        content = (
            "<p>keep me</p>"
            "<!-- blog-picks-block:v1 -->blog<!-- /blog-picks-block -->"
            f"{recipe_block.BLOCK_MARKER_OPEN}recipe{recipe_block.BLOCK_MARKER_CLOSE}"
        )
        assert prose_without_blocks(content) == "<p>keep me</p>"


class TestRenderForMode:
    def _post(self, body: str = _BODY) -> WpPost:
        return WpPost(id=1, slug="dog-gps-guide", title="t", content=body, meta={})

    def test_insert_mode_uses_blog_wording(self):
        block, new = render_for_mode(self._post(), [_POOL["gps-tracker"]], "insert", "dff-20")
        assert "<h2>Gear I Actually Use</h2>" in block
        assert new.index("blog-picks-block") < new.index(">FAQ<")

    def test_recipe_mode_replaces_between_recipe_markers(self):
        body = (
            "<p>Treats.</p>"
            f"{recipe_block.BLOCK_MARKER_OPEN}\n<ul><li>old</li></ul>\n"
            f"{recipe_block.BLOCK_MARKER_CLOSE}"
        )
        block, new = render_for_mode(
            self._post(body), [_POOL["run-harness"]], MODE_REFRESH_RECIPE, "dff-20"
        )
        assert block.startswith(recipe_block.BLOCK_MARKER_OPEN)
        assert "<h2>Our Pick: Tools Used in This Recipe</h2>" in block
        assert new.count(recipe_block.BLOCK_MARKER_OPEN) == 1
        assert "blog-picks-block" not in new
        assert "<li>old</li>" not in new

    def test_an_unpublished_draft_gets_a_campaign_id_derived_from_its_title(self):
        """WordPress reports `slug: ""` for an unpublished post, and this
        module can address drafts by id -- so `post.slug` is empty for exactly
        the posts the drafting crew produces. Rendering with it wrote a dead
        `ascsubtag=blog-` onto two live posts (4289, 4295)."""
        draft = WpPost(id=1, slug="", title="Best Dog GPS Trackers 2026", content=_BODY, meta={})
        block, _ = render_for_mode(draft, [_POOL["gps-tracker"]], "insert", "dff-20")
        assert "ascsubtag=blog-best-dog-gps-trackers-2026" in block
        assert "ascsubtag=blog-&" not in block
        assert 'ascsubtag=blog-"' not in block

    def test_disclosure_is_omitted_when_the_posts_own_prose_has_one(self):
        body = _BODY + "<p>Affiliate disclosure: we may earn a commission.</p>"
        block, _ = render_for_mode(self._post(body), [_POOL["gps-tracker"]], "insert", "dff-20")
        assert "As an Amazon Associate" not in block

    def test_a_previous_blocks_disclosure_does_not_suppress_the_next_one(self):
        # Otherwise re-runs would toggle the disclosure and never converge.
        _, once = render_for_mode(self._post(), [_POOL["gps-tracker"]], "insert", "dff-20")
        block, twice = render_for_mode(
            self._post(once), [_POOL["gps-tracker"]], "refresh-blog", "dff-20"
        )
        assert "As an Amazon Associate" in block
        assert twice == once, "a second pass must converge, not flip-flop"


class TestSelection:
    def test_selector_failure_returns_none_not_a_fallback_catalog(self):
        assert select_products_for_existing_post(_POOL, _brief(), execute_fn=lambda *_a: None) is (
            None
        )

    def test_empty_selection_is_an_empty_dict_not_none(self):
        assert select_products_for_existing_post(_POOL, _brief(), execute_fn=_fake_execute()) == {}

    def test_empty_pool_short_circuits_to_no_fit(self):
        assert select_products_for_existing_post({}, _brief(), execute_fn=_fake_execute("x")) == {}

    def test_unknown_keys_are_dropped(self):
        picked = select_products_for_existing_post(
            _POOL, _brief(), execute_fn=_fake_execute("gps-tracker", "not-in-catalog")
        )
        assert picked is not None
        assert list(picked) == ["gps-tracker"]

    def test_selection_is_capped(self):
        picked = select_products_for_existing_post(
            _POOL,
            _brief(),
            execute_fn=_fake_execute("gps-tracker", "run-harness", "cooling-vest"),
            max_products=2,
        )
        assert picked is not None
        assert len(picked) == 2

    def test_usage_counts_order_candidates_least_used_first(self):
        seen: list[str] = []
        select_products_for_existing_post(
            _POOL,
            _brief(),
            usage_counts={"gps-tracker": 3, "run-harness": 1},
            execute_fn=_fake_execute("gps-tracker", seen=seen),
        )
        listing = seen[0].split("category | notes)")[1]
        assert (
            listing.index("cooling-vest")
            < listing.index("run-harness")
            < listing.index("gps-tracker")
        )

    def test_candidate_lines_keep_the_selector_prompt_format(self):
        seen: list[str] = []
        select_products_for_existing_post(_POOL, _brief(), execute_fn=_fake_execute(seen=seen))
        assert (
            "gps-tracker | PawTrack GPS Collar | gps-tracker | no-subscription tracker" in seen[0]
        )
        # None category/notes render as empty fields, never the string "None".
        assert "cooling-vest | ChillPup Cooling Vest |  | " in seen[0]


# ── recently-used products are excluded, not just ranked ─────────────────────


def _entry(key: str) -> ProductEntry:
    return ProductEntry(key=key, asin=f"B{key[:9].upper():0<9}", display=key)


def _pool(*keys: str) -> dict[str, ProductEntry]:
    return {k: _entry(k) for k in keys}


def _picker(*keys: str):
    def _run(_agent: object, _task: object) -> ProductSelection:
        return ProductSelection(products=[SelectedProduct(key=k, reason="fits") for k in keys])

    return _run


def test_a_used_product_is_removed_from_the_candidate_list() -> None:
    """Ranking it last was not enough: the model still picked
    `iris-weatherpro-33qt` for two storage posts in a row."""
    captured: list[str] = []

    def _capture(_agent: object, task: object) -> ProductSelection:
        captured.append(task.description)  # type: ignore[attr-defined]
        return ProductSelection()

    select_products_for_existing_post(
        _pool("used-one", "fresh-a", "fresh-b", "fresh-c", "fresh-d"),
        _brief(),
        usage_counts={"used-one": 1},
        execute_fn=_capture,
    )

    assert "used-one |" not in captured[0]
    assert "fresh-a |" in captured[0]


def test_a_used_product_cannot_be_picked_even_if_the_model_names_it() -> None:
    result = select_products_for_existing_post(
        _pool("used-one", "fresh-a", "fresh-b", "fresh-c", "fresh-d"),
        _brief(),
        usage_counts={"used-one": 1},
        execute_fn=_picker("used-one", "fresh-a"),
    )

    assert result == {"fresh-a": _entry("fresh-a")}


def test_exclusion_relaxes_rather_than_starving_a_small_pool() -> None:
    """A brand with few products must not be reduced to empty prompts."""
    result = select_products_for_existing_post(
        _pool("used-one", "fresh-a"),
        _brief(),
        usage_counts={"used-one": 1, "fresh-a": 1},
        execute_fn=_picker("used-one"),
    )

    assert result == {"used-one": _entry("used-one")}
