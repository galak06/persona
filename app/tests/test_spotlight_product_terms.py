"""`lib.crew.spotlight.product_terms` -- the product vocabulary the spotlight
prompt, rules, excerpt and API badge all share. Pure functions, no fakes."""

from __future__ import annotations

import pytest

from lib.affiliate_resolver import ProductEntry
from lib.crew.spotlight.product_terms import (
    brand_token,
    brand_token_re,
    display_name,
    is_certification_verified,
    mentions_brand,
    verified_certifications,
)


def _product(display: str, *, key: str = "k", notes: str | None = None) -> ProductEntry:
    return ProductEntry(key=key, asin="B006W6YHHI", display=display, notes=notes)


@pytest.mark.parametrize(
    ("display", "expected"),
    [
        ("Greenies Regular Dental Dog Treats (36 ct)", "greenies"),
        ("Canine Greenies", "greenies"),
        ("Pet Honesty Dental Wipes", "honesty"),
        ("Fi Series 3+ Smart Dog Collar", "fi"),
        ("Apple AirTag (4-pack)", "apple"),
        # Every word generic: the first one anyway, never "".
        ("Dog Pet", "dog"),
    ],
)
def test_brand_token_skips_generic_openers(display: str, expected: str) -> None:
    assert brand_token(_product(display)) == expected


def test_display_name_drops_the_pack_size_and_falls_back_to_the_key() -> None:
    assert display_name(_product("Greenies Dental Treats (36 ct)")) == "Greenies Dental Treats"
    assert display_name(_product("", key="greenies-dental-treats")) == "greenies dental treats"
    assert brand_token(_product("", key="greenies-dental-treats")) == "greenies"


def test_blank_product_has_no_token_and_matches_nothing() -> None:
    blank = _product("", key="")
    assert brand_token(blank) == ""
    assert brand_token_re(blank) is None
    assert mentions_brand("Greenies are great", blank) is False


def test_mentions_brand_is_whole_word_and_tolerates_plural_and_possessive() -> None:
    fi = _product("Fi Series 3+ Smart Dog Collar")
    # The reason the matcher is whole-word: a two-letter token inside a word.
    assert mentions_brand("Find the first light of the morning", fi) is False
    assert mentions_brand("Nalla wears her Fi on every run", fi) is True
    assert mentions_brand("The Fi's battery lasts weeks", fi) is True

    greenies = _product("Greenies Regular Dental Dog Treats")
    assert mentions_brand("One GREENIES chew a day", greenies) is True
    assert mentions_brand("", greenies) is False


def test_certification_needs_the_operator_marker() -> None:
    verified = _product("Greenies", notes="VOHC accepted. Verified against vohc.org 2026-08.")
    unverified = _product("Mystery Chew", notes="Says VOHC on the bag.")
    assert is_certification_verified(verified) is True
    assert is_certification_verified(unverified) is False
    assert is_certification_verified(_product("No Note")) is False


def test_verified_certifications_names_only_what_the_note_names() -> None:
    verified = _product("Greenies", notes="VOHC accepted. Verified against vohc.org 2026-08.")
    # A verified VOHC note does not license an AAFCO claim.
    assert verified_certifications(verified) == ("VOHC",)
    # The marker is required even when a body is named.
    assert verified_certifications(_product("Chew", notes="VOHC seal on the bag")) == ()
