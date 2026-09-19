"""Tests for `lib.crew.spotlight.prompts` -- the spotlight writer's brief.

The prompt is the ONLY thing standing between a paid crew run and a caption
that breaks Amazon's operating agreement, and every rule it carries has a
matching blocking rule in `lib.crew.spotlight.rules`. So these tests are less
about wording than about the two ways that pairing goes wrong:

* a rule the prompt never states -- the writer cannot satisfy it, and all three
  attempts burn against it;
* a fact the prompt leaks -- most dangerously the ASIN, which is the one field
  that turns a caption into a link-bearing caption (both platforms suppress
  those) without the writer having done anything obviously wrong.

Pure string assertions: `build_spotlight_task_description` does no I/O.
"""
# ruff: noqa: S101

from __future__ import annotations

from lib.affiliate_resolver import ProductEntry
from lib.crew.spotlight.prompts import (
    DISCLOSURE_SENTENCE,
    KEYWORD_MAX_TERMS,
    build_spotlight_task_description,
    spotlight_keyword,
)

_VERIFIED = ProductEntry(
    key="greenies-regular",
    asin="B0009YWQ2E",
    display="Greenies Regular Dental Dog Treats (original chicken, 36 ct)",
    category="dental-treats",
    notes="Verified against the VOHC accepted product list, checked 2026-03.",
)
_UNVERIFIED = ProductEntry(
    key="kong-classic",
    asin="B0002AR0I8",
    display="KONG Classic Dog Toy (large)",
    category="enrichment",
    notes="Natural rubber, dishwasher safe.",
)
_FACTS = "Nalla is a shepherd mix, about 50 lb, and eats a chicken-based kibble."


def _description(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "title": "How Dental Treats Actually Work",
        "body": "Excerpt from the published post.",
        "product": _VERIFIED,
        "site_domain": "dogfoodandfun.com",
        "brand_voice": "Warm, honest, data-driven.",
        "mascot_facts": _FACTS,
        "photo_is_owner_pick": False,
        "product_in_post": True,
    }
    kwargs.update(overrides)
    return build_spotlight_task_description(**kwargs)  # type: ignore[arg-type]


# ── spotlight_keyword ─────────────────────────────────────────────────────


def test_keyword_drops_the_parenthetical_pack_size() -> None:
    """The live example. "(original chicken, 36 ct)" is catalog metadata: in a
    caption's first sentence it reads as a typo, and `find_caption_violations`
    would demand two thirds of those words be in it."""
    assert spotlight_keyword(_VERIFIED) == "Greenies Regular Dental Dog Treats"


def test_keyword_caps_at_the_term_limit() -> None:
    long_name = ProductEntry(
        key="long",
        asin="B1",
        display="Wellness Complete Health Natural Dry Small Breed Dog Food",
        category="food",
    )
    keyword = spotlight_keyword(long_name)
    assert keyword == "Wellness Complete Health Natural Dry"
    assert len(keyword.split()) == KEYWORD_MAX_TERMS


def test_keyword_falls_back_to_the_catalog_key() -> None:
    assert spotlight_keyword(ProductEntry(key="fi-collar", asin="B1", display="")) == "fi collar"


# ── what the prompt must never leak ───────────────────────────────────────


def test_prompt_never_leaks_the_asin_a_link_or_a_price() -> None:
    description = _description()
    assert _VERIFIED.asin not in description
    assert "/dp/" not in description
    assert "http" not in description
    for currency in ("$", "£", "€"):
        assert currency not in description


# ── the product, the CTA, the disclosure ──────────────────────────────────


def test_prompt_keeps_the_base_brief_and_uses_the_spotlight_keyword() -> None:
    """Appending rather than forking is the whole design: every improvement to
    the base brief has to keep reaching spotlights."""
    description = _description()
    assert "## The single most important rule: answer first" in description
    assert spotlight_keyword(_VERIFIED) in description


def test_prompt_names_one_product_with_its_category_and_note() -> None:
    description = _description()
    assert "Greenies Regular Dental Dog Treats" in description
    assert "dental-treats" in description
    assert "Verified against the VOHC accepted product list" in description
    assert "Write about this product and nothing else" in description
    assert "must NAME the product in words" in description


def test_prompt_states_the_comment_cta_and_forbids_a_bot() -> None:
    description = _description()
    assert "Comment {comment_keyword} and I'll DM you the link" in description
    assert "NEVER promise a bot" in description


def test_prompt_puts_the_disclosure_in_both_captions_and_says_where() -> None:
    """The contract's named conflict: the base brief demands a closing
    question, so the override has to settle the ordering explicitly rather
    than leave the model to drop one of the two rules."""
    description = _description()
    assert DISCLOSURE_SENTENCE in description
    assert "OWN line AFTER it" in description
    assert "BEFORE the hashtag line" in description
    assert "A reworded disclosure is a missing disclosure." in description


# ── grounding ─────────────────────────────────────────────────────────────


def test_prompt_grounds_claims_and_forbids_unsourced_mascot_usage() -> None:
    description = _description()
    assert "## Grounding: where every claim may come from" in description
    assert _FACTS in description
    assert "NEVER say the dog eats, chews, uses, wears, loves" in description


def test_prompt_omits_the_mascot_block_when_the_facts_file_is_empty() -> None:
    """An empty header invites the model to fill it in."""
    description = _description(mascot_facts="")
    assert "What is actually true about the brand's dog" not in description
    # The prohibition itself does not depend on having facts -- with none, the
    # only honest answer is "say nothing about the dog and this product".
    assert "NEVER say the dog eats" in description


def test_prompt_warns_when_the_article_does_not_mention_the_product() -> None:
    assert "This product is NOT linked or discussed in that article" in _description(
        product_in_post=False
    )


def test_prompt_omits_the_not_in_post_warning_when_it_is_in_the_post() -> None:
    assert "NOT linked or discussed" not in _description(product_in_post=True)


# ── certifications ────────────────────────────────────────────────────────


def test_prompt_licenses_only_the_verified_certification() -> None:
    description = _description()
    assert "## Certifications: VOHC ONLY" in description
    assert '"VOHC-accepted"' in description
    # One registry was checked; the other two were not, and saying so is what
    # stops a verified product from collecting the whole set.
    assert "naming AAFCO, NASC is forbidden" in description


def test_prompt_forbids_every_certification_when_none_is_verified() -> None:
    description = _description(product=_UNVERIFIED)
    assert "## Certifications: name NONE" in description
    assert "Do not write VOHC, AAFCO, NASC" in description
    assert "you MAY say so" not in description


# ── the image ─────────────────────────────────────────────────────────────


def test_prompt_keeps_the_image_brief_to_the_scene() -> None:
    description = _description()
    assert "## The image: the SCENE only, never the product" in description
    for banned in ("packaging", "a label", "a logo", "a bag", "a box", "brand text"):
        assert banned in description
    # The brand word is named so the writer knows which word to avoid.
    assert 'the word "greenies"' in description


def test_prompt_adds_the_owner_pick_clause_only_when_the_owner_picked() -> None:
    clause = "Do NOT redraw or restyle anything already in the reference photo"
    assert clause in _description(photo_is_owner_pick=True)
    assert clause not in _description(photo_is_owner_pick=False)


def test_prompt_keeps_retailers_and_prices_off_the_ribbon() -> None:
    description = _description()
    assert "may not name a retailer or a store" in description
    assert "may not carry a price, a discount or a percentage" in description
