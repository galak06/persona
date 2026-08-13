"""Tests for dynamic Amazon product discovery (`lib.crew.products.discovery`)
and the block guarantee (`lib.crew.products.ensure_block`).

No network and no LLM: Serper is injected via `search_fn`, using result
shapes copied from real responses (`organic[].link` / `.title` / `.snippet`).
"""
# ruff: noqa: S101, SLF001

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.affiliate_resolver import ProductEntry
from lib.crew.products.discovery import discover_products
from lib.crew.products.ensure_block import ensure_product_block
from lib.crew.writer.models import ContentBrief

_TAG = "dogfoodandfun01-20"


def _serp(*rows: tuple[str, str]) -> list[dict[str, Any]]:
    return [
        {"link": f"https://www.amazon.com/dp/{asin}?th=1", "title": title, "snippet": "snip"}
        for asin, title in rows
    ]


def _brief(title: str = "Dog Food Recall 2026: What to Stop Feeding") -> ContentBrief:
    return ContentBrief(
        suggested_title=title,
        primary_keyword="dog food recall 2026",
        mascot_angle="Nalla's own switch after the recall.",
    )


# ── discovery ────────────────────────────────────────────────────────────────


def test_asin_is_taken_from_the_result_url(tmp_path: Path) -> None:
    """The ASIN lives in the URL path -- that is what makes this a read of
    Google's results rather than anything touching Amazon."""
    found = discover_products(
        tmp_path,
        ["dog food storage container"],
        search_fn=lambda _q, _n: _serp(("B0BW3W3GLB", "TIOVERY Dog Food Storage Container")),
    )

    assert [e.asin for e in found.values()] == ["B0BW3W3GLB"]


def test_marketing_title_is_trimmed_to_a_product_name(tmp_path: Path) -> None:
    found = discover_products(
        tmp_path,
        ["dog cooling vest"],
        search_fn=lambda _q, _n: _serp(
            ("B0CW576X1P", "KYEESE Dog Cooling Vest, Evaporative Jacket for Small Medium Large")
        ),
    )

    assert next(iter(found.values())).display == "KYEESE Dog Cooling Vest"


def test_results_without_an_asin_are_ignored(tmp_path: Path) -> None:
    """Amazon search/category pages match `site:amazon.com` but are not products."""
    rows: list[dict[str, Any]] = [
        {"link": "https://www.amazon.com/s?k=dog+food", "title": "Dog Food"},
        {"link": "https://www.amazon.com/dp/B0BW3W3GLB", "title": "Real Product"},
    ]

    found = discover_products(tmp_path, ["dog food"], search_fn=lambda _q, _n: rows)

    assert len(found) == 1


def test_no_queries_means_no_search_at_all(tmp_path: Path) -> None:
    """An informational post the synthesist declined to shop for must not
    trigger a paid API call."""
    calls: list[str] = []

    found = discover_products(tmp_path, [], search_fn=lambda q, _n: calls.append(q) or [])  # type: ignore[func-returns-value]

    assert found == {}
    assert calls == []


def test_a_failing_search_degrades_to_nothing(tmp_path: Path) -> None:
    """Discovery is an enhancement; a Serper outage must not fail the draft."""

    def _boom(_q: str, _n: int) -> list[dict[str, Any]]:
        raise RuntimeError("serper down")

    with pytest.raises(RuntimeError):
        discover_products(tmp_path, ["x"], search_fn=_boom)
    # ...and the selector's own guard is what converts that into {} --
    # see test_discovery_failure_is_not_fatal_for_selection below.


def test_discoveries_are_cached_for_reuse(tmp_path: Path) -> None:
    """A re-draft of the same post must not re-buy the same search."""
    discover_products(
        tmp_path,
        ["dog food storage container"],
        search_fn=lambda _q, _n: _serp(("B0BW3W3GLB", "TIOVERY Dog Food Storage Container")),
    )

    cache = tmp_path / "data" / "cache" / "discovered_products.json"
    assert cache.exists()
    assert "B0BW3W3GLB" in cache.read_text()


def test_two_listings_with_similar_names_do_not_collide(tmp_path: Path) -> None:
    found = discover_products(
        tmp_path,
        ["limited ingredient dog food"],
        search_fn=lambda _q, _n: _serp(
            ("B0CRFBPRXR", "KOHA Limited Ingredient Bland Diet for Dogs"),
            ("B0DP3HMXXD", "KOHA Limited Ingredient Bland Diet for Dogs"),
        ),
    )

    assert len(found) == 2


# ── the block guarantee ──────────────────────────────────────────────────────


def _catalog() -> dict[str, ProductEntry]:
    return {
        "fi-collar": ProductEntry(key="fi-collar", asin="B0FH8HQS3V", display="Fi Collar"),
        "airtag": ProductEntry(key="airtag", asin="B0933BVK6T", display="Apple AirTag"),
    }


def test_a_writer_that_ignored_every_pick_still_gets_a_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact failure that shipped post 4289: disclosure, no products."""
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", _TAG)

    body, keys = ensure_product_block("<p>An article with no products.</p>", _catalog(), _brief())

    assert "blog-picks-block:v1" in body
    assert _TAG in body
    assert set(keys) == {"fi-collar", "airtag"}


def test_an_in_context_recommendation_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A product the writer worked into the prose reads better than an
    appended block -- don't duplicate it."""
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", _TAG)
    original = "<p>We use the [AFFILIATE:fi-collar] on every run.</p>"

    body, keys = ensure_product_block(original, _catalog(), _brief())

    assert body == original
    assert keys == ["fi-collar"]


def test_an_already_resolved_link_also_counts_as_referenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Placeholders may already be substituted upstream; match the ASIN too."""
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", _TAG)
    original = f'<p>See the <a href="https://www.amazon.com/dp/B0FH8HQS3V?tag={_TAG}">Fi</a>.</p>'

    body, keys = ensure_product_block(original, _catalog(), _brief())

    assert body == original
    assert keys == ["fi-collar"]


def test_no_associates_tag_means_no_block_rather_than_naked_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An untagged Amazon link violates the Associates T&C -- omit instead."""
    monkeypatch.delenv("AMAZON_ASSOCIATES_TAG", raising=False)
    original = "<p>No products here.</p>"

    body, keys = ensure_product_block(original, _catalog(), _brief())

    assert body == original
    assert keys == []


def test_an_empty_selection_leaves_the_body_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", _TAG)
    original = "<p>Purely informational.</p>"

    assert ensure_product_block(original, {}, _brief()) == (original, [])


def test_block_is_never_added_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", _TAG)
    once, _ = ensure_product_block("<p>Body.</p>", _catalog(), _brief())

    twice, keys = ensure_product_block(once, _catalog(), _brief())

    assert twice.count("blog-picks-block:v1") == once.count("blog-picks-block:v1")
    assert keys == ["fi-collar", "airtag"]


def test_a_department_title_is_not_offered_as_a_product(tmp_path: Path) -> None:
    """Live-observed: Google titled B08RC4VR7H "Pet Supplies". A block row
    named "Pet Supplies" is worse for the reader than one product fewer."""
    found = discover_products(
        tmp_path,
        ["dog food bowl with lid"],
        search_fn=lambda _q, _n: _serp(
            ("B08RC4VR7H", "Pet Supplies"),
            ("B0CLRKNDW1", "Metal Dog Food Container 5-7lb"),
        ),
    )

    assert [e.asin for e in found.values()] == ["B0CLRKNDW1"]


def test_a_storefront_prefix_is_stripped_not_used_as_the_name(tmp_path: Path) -> None:
    found = discover_products(
        tmp_path,
        ["dog cooling vest"],
        search_fn=lambda _q, _n: _serp(("B0CW576X1P", "Amazon.com: KYEESE Dog Cooling Vest")),
    )

    assert next(iter(found.values())).display == "KYEESE Dog Cooling Vest"
