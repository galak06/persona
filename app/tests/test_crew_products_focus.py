"""The focus gate: which catalog entries may be promoted during a focus run.

Two behaviours carry the feature and are asserted from both sides here --
that a declared focus admits ONLY explicitly selected products (no inference
from the `category` tag, which is what makes selection a decision rather than
a side effect of tagging), and that an undeclared focus falls back to the
pre-focus behaviour of offering everything still active.
"""
# ruff: noqa: S101

from __future__ import annotations

from lib.affiliate_resolver import ProductEntry
from lib.crew.products.focus import focus_pool, slugify_category


def entry(key: str, **kwargs: object) -> ProductEntry:
    """A catalog entry with sane defaults for everything under test."""
    return ProductEntry(
        key=key,
        asin="B000000000",
        display=key.replace("-", " ").title(),
        category=kwargs.get("category"),  # type: ignore[arg-type]
        notes=None,
        active=bool(kwargs.get("active", True)),
        selected_for=tuple(kwargs.get("selected_for", ())),  # type: ignore[arg-type]
    )


class TestSlugifyCategory:
    def test_display_and_slug_spellings_converge(self) -> None:
        assert slugify_category("Dental Care") == slugify_category("dental-care")

    def test_folds_accents_rather_than_dropping_them(self) -> None:
        assert slugify_category("Éclat") == "eclat"

    def test_blank_is_empty(self) -> None:
        assert slugify_category("   ") == ""
        assert slugify_category("") == ""


class TestNoFocusDeclared:
    def test_returns_everything_active(self) -> None:
        pool = {"a": entry("a"), "b": entry("b")}
        assert set(focus_pool(pool, "")) == {"a", "b"}

    def test_still_drops_inactive_entries(self) -> None:
        """Deactivation is about the product, not about the campaign."""
        pool = {"a": entry("a"), "b": entry("b", active=False)}
        assert set(focus_pool(pool, "")) == {"a"}


class TestFocusDeclared:
    def test_admits_only_explicitly_selected_products(self) -> None:
        pool = {
            "picked": entry("picked", selected_for=("Dental Care",)),
            "unpicked": entry("unpicked"),
        }
        assert set(focus_pool(pool, "Dental Care")) == {"picked"}

    def test_category_tag_alone_does_not_grant_selection(self) -> None:
        """The whole point of explicit selection: tagging is not consent.

        A product tagged `dental-care` but never selected must stay out,
        otherwise re-tagging the catalog would silently enrol products into a
        live campaign.
        """
        pool = {"tagged": entry("tagged", category="dental-care")}
        assert focus_pool(pool, "Dental Care") == {}

    def test_matches_across_the_two_spellings(self) -> None:
        """Registry says "Dental Care"; a hand-edited file says "dental-care"."""
        pool = {"picked": entry("picked", selected_for=("dental-care",))}
        assert set(focus_pool(pool, "Dental Care")) == {"picked"}

    def test_selected_but_inactive_is_excluded(self) -> None:
        pool = {"gone": entry("gone", active=False, selected_for=("Dental Care",))}
        assert focus_pool(pool, "Dental Care") == {}

    def test_selection_for_another_category_does_not_leak(self) -> None:
        pool = {"groom": entry("groom", selected_for=("Grooming",))}
        assert focus_pool(pool, "Dental Care") == {}

    def test_empty_selection_is_a_legitimate_empty_pool(self) -> None:
        """Not an error -- the selector's empty-pool branch omits the block."""
        pool = {"a": entry("a"), "b": entry("b")}
        assert focus_pool(pool, "Dental Care") == {}

    def test_a_product_can_be_selected_for_several_focus_runs(self) -> None:
        both = entry("both", selected_for=("Dental Care", "Grooming"))
        assert set(focus_pool({"both": both}, "Grooming")) == {"both"}
        assert set(focus_pool({"both": both}, "Dental Care")) == {"both"}
