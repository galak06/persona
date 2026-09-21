"""Writing to a brand's affiliate catalog from the products panel.

Everything here runs against a real file in a tmp brand, not a mock, because
the two properties worth asserting are both about what ends up on disk: that
an edit preserves the hand-written fields the store has never heard of, and
that selecting a product for a category really does take it away from the one
that held it.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.crew.products.catalog_store import (
    CatalogStoreError,
    add_product,
    catalog_path,
    load_raw,
    set_focus_selection,
    update_product,
)

FOCUS = "Dental Care"


@pytest.fixture
def brand(tmp_path: Path) -> Path:
    """A brand directory whose catalog already holds two products."""
    path = catalog_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "_note": "hand-written format note that must survive edits",
                    "key": "brush-a",
                    "asin": "B000000001",
                    "display": "Brush A",
                    "category": "dental-care",
                    "sourced_internal": "/dental-guide/",
                },
                {
                    "key": "brush-b",
                    "asin": "B000000002",
                    "display": "Brush B",
                    "category": "dental-care",
                },
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


def stored(brand_dir: Path, key: str) -> dict:
    """The entry for `key` as it currently sits on disk."""
    return next(e for e in load_raw(brand_dir) if e["key"] == key)


class TestLoadRaw:
    def test_missing_catalog_reads_as_empty(self, tmp_path: Path) -> None:
        assert load_raw(tmp_path) == []

    def test_malformed_catalog_reads_as_empty(self, tmp_path: Path) -> None:
        path = catalog_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert load_raw(tmp_path) == []

    def test_non_dict_members_are_skipped(self, tmp_path: Path) -> None:
        path = catalog_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(["stray", {"key": "real"}]), encoding="utf-8")
        assert load_raw(tmp_path) == [{"key": "real"}]


class TestAddProduct:
    def test_appends_and_defaults_to_active_unselected(self, brand: Path) -> None:
        add_product(brand, key="floss-c", asin="B000000003", display="Floss C")
        entry = stored(brand, "floss-c")
        assert entry["active"] is True
        assert entry["selected_for"] == []

    def test_rejects_a_duplicate_key(self, brand: Path) -> None:
        with pytest.raises(CatalogStoreError, match="already exists"):
            add_product(brand, key="brush-a", asin="B000000009", display="Dupe")

    def test_rejects_a_key_the_placeholder_grammar_cannot_reference(self, brand: Path) -> None:
        with pytest.raises(CatalogStoreError, match="invalid product key"):
            add_product(brand, key="Brush A!", asin="B000000009", display="Bad")

    def test_rejects_a_malformed_asin(self, brand: Path) -> None:
        with pytest.raises(CatalogStoreError, match="invalid ASIN"):
            add_product(brand, key="floss-c", asin="nope", display="Bad")

    def test_select_for_displaces_the_incumbent(self, brand: Path) -> None:
        set_focus_selection(brand, "brush-a", FOCUS, selected=True)
        add_product(brand, key="floss-c", asin="B000000003", display="Floss C", select_for=FOCUS)
        assert stored(brand, "brush-a")["selected_for"] == []
        assert stored(brand, "floss-c")["selected_for"] == [FOCUS]


class TestUpdateProduct:
    def test_preserves_fields_the_store_has_never_heard_of(self, brand: Path) -> None:
        """A rebuild-from-known-fields writer would drop these silently."""
        update_product(brand, "brush-a", display="Brush A v2")
        entry = stored(brand, "brush-a")
        assert entry["display"] == "Brush A v2"
        assert entry["_note"].startswith("hand-written")
        assert entry["sourced_internal"] == "/dental-guide/"

    def test_none_means_leave_alone(self, brand: Path) -> None:
        update_product(brand, "brush-a", notes="a note")
        update_product(brand, "brush-a", active=False)
        entry = stored(brand, "brush-a")
        assert entry["notes"] == "a note"
        assert entry["active"] is False

    def test_unknown_key_is_refused(self, brand: Path) -> None:
        with pytest.raises(CatalogStoreError, match="no such product key"):
            update_product(brand, "ghost", display="nope")


class TestSetFocusSelection:
    def test_selecting_takes_the_category_from_the_incumbent(self, brand: Path) -> None:
        set_focus_selection(brand, "brush-a", FOCUS, selected=True)
        set_focus_selection(brand, "brush-b", FOCUS, selected=True)
        assert stored(brand, "brush-a")["selected_for"] == []
        assert stored(brand, "brush-b")["selected_for"] == [FOCUS]

    def test_deselecting_leaves_the_category_unowned(self, brand: Path) -> None:
        set_focus_selection(brand, "brush-a", FOCUS, selected=True)
        set_focus_selection(brand, "brush-a", FOCUS, selected=False)
        assert stored(brand, "brush-a")["selected_for"] == []
        assert stored(brand, "brush-b")["selected_for"] == []

    def test_selections_for_other_categories_survive(self, brand: Path) -> None:
        """Rotating the focus away and back must find the old pick standing."""
        set_focus_selection(brand, "brush-a", "Grooming", selected=True)
        set_focus_selection(brand, "brush-a", FOCUS, selected=True)
        set_focus_selection(brand, "brush-b", FOCUS, selected=True)
        assert stored(brand, "brush-a")["selected_for"] == ["Grooming"]

    def test_matches_an_existing_selection_across_spellings(self, brand: Path) -> None:
        set_focus_selection(brand, "brush-a", "dental-care", selected=True)
        set_focus_selection(brand, "brush-b", "Dental Care", selected=True)
        assert stored(brand, "brush-a")["selected_for"] == []

    def test_blank_category_is_refused(self, brand: Path) -> None:
        with pytest.raises(CatalogStoreError, match="blank category"):
            set_focus_selection(brand, "brush-a", "  ", selected=True)

    def test_unknown_key_is_refused(self, brand: Path) -> None:
        with pytest.raises(CatalogStoreError, match="no such product key"):
            set_focus_selection(brand, "ghost", FOCUS, selected=True)
