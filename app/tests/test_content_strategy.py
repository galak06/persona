"""One-focus-category strategy: the policy module and the insert-time gate.

The gate is the only thing standing between "the prompt asked the model to
stay in one category" and "ideas outside that category cannot be stored", so
these tests care most about the ways it could silently let something through
(a blank category, a case/spacing variant) or silently reject everything (a
brand with no focus at all).
"""

from __future__ import annotations

from typing import Any

import pytest

from lib import ideas_db
from lib.content_strategy import (
    BREADTH_CLAUSE,
    ContentStrategy,
    focus_clause,
    is_in_focus,
    load_content_strategy,
    normalize_category,
)

# ─────────────────────────────────────────────────────── policy: loading


def test_missing_or_malformed_block_is_no_focus() -> None:
    for raw in ({}, {"content_strategy": None}, {"content_strategy": "nope"}, None):
        strategy = load_content_strategy(raw)  # type: ignore[arg-type]
        assert strategy.focus_category == ""
        assert not strategy.has_focus


def test_focus_category_is_trimmed_on_load() -> None:
    strategy = load_content_strategy({"content_strategy": {"focus_category": "  Dog Food  "}})
    assert strategy.focus_category == "Dog Food"
    assert strategy.has_focus


def test_whitespace_only_focus_is_not_a_focus() -> None:
    """A field the operator blanked out must not become a category that
    matches nothing -- that would reject every idea instead of disabling."""
    strategy = load_content_strategy({"content_strategy": {"focus_category": "   "}})
    assert not strategy.has_focus


def test_non_bool_depth_bias_falls_back_to_default() -> None:
    strategy = load_content_strategy(
        {"content_strategy": {"focus_category": "X", "depth_bias": "yes"}}
    )
    assert strategy.depth_bias is True


# ─────────────────────────────────────────────────────── policy: matching


@pytest.mark.parametrize(
    "candidate",
    ["Dog Food", "dog food", "DOG FOOD", "  Dog   Food  "],
)
def test_matching_is_case_and_whitespace_insensitive(candidate: str) -> None:
    strategy = ContentStrategy(focus_category="Dog Food")
    assert is_in_focus(candidate, strategy)


@pytest.mark.parametrize("candidate", ["Gear", "Dog Foods", "Food", "", None])
def test_out_of_focus_categories_are_rejected(candidate: str | None) -> None:
    """`Dog Foods` is deliberately a MISS: singularising would silently merge
    two categories a real site keeps apart. A blank category is a miss too --
    a missing category is not evidence of belonging to the focused one."""
    strategy = ContentStrategy(focus_category="Dog Food")
    assert not is_in_focus(candidate, strategy)


@pytest.mark.parametrize("candidate", ["anything", "", None])
def test_no_focus_accepts_everything(candidate: str | None) -> None:
    assert is_in_focus(candidate, ContentStrategy())


def test_normalize_category_handles_none() -> None:
    assert normalize_category(None) == ""


# ─────────────────────────────────────────────────────── policy: prompt clause


def test_unfocused_brand_keeps_the_original_breadth_instruction() -> None:
    """Byte-for-byte: a brand that never opts in must get the exact prompt it
    got before focus existed."""
    assert focus_clause(ContentStrategy()) == BREADTH_CLAUSE


def test_focused_clause_names_the_category_and_asks_for_depth() -> None:
    clause = focus_clause(ContentStrategy(focus_category="Dog Food"))
    assert '"Dog Food"' in clause
    assert "Depth is the point" in clause
    assert "Breadth is the point" not in clause


def test_depth_bias_off_still_constrains_the_category() -> None:
    clause = focus_clause(ContentStrategy(focus_category="Dog Food", depth_bias=False))
    assert '"Dog Food"' in clause
    assert "Depth is the point" not in clause


# ─────────────────────────────────────────────────────── the insert gate


@pytest.fixture
def captured_inserts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture what `insert_idea` would write, without touching Postgres."""
    rows: list[dict[str, Any]] = []

    def fake_execute(query: str, params: dict[str, Any]) -> int:
        rows.append(params)
        return 1

    monkeypatch.setattr(ideas_db.db, "execute", fake_execute)
    return rows


def _set_focus(monkeypatch: pytest.MonkeyPatch, focus: str) -> None:
    monkeypatch.setattr(ideas_db.brands_db, "focus_category", lambda _brand_id: focus)


def test_gate_rejects_an_out_of_focus_idea(
    monkeypatch: pytest.MonkeyPatch, captured_inserts: list[dict[str, Any]]
) -> None:
    _set_focus(monkeypatch, "Dog Food")
    result = ideas_db.insert_idea({"Category": "Gear", "Topic": "Best GPS"}, brand_id="acme")
    assert result is None
    assert captured_inserts == []


def test_gate_admits_an_in_focus_idea_despite_case_and_spacing(
    monkeypatch: pytest.MonkeyPatch, captured_inserts: list[dict[str, Any]]
) -> None:
    _set_focus(monkeypatch, "Dog Food")
    result = ideas_db.insert_idea(
        {"Category": "  dog   food ", "Topic": "Raw vs kibble"}, brand_id="acme"
    )
    assert result is not None
    assert len(captured_inserts) == 1


def test_gate_rejects_a_blank_category_when_focused(
    monkeypatch: pytest.MonkeyPatch, captured_inserts: list[dict[str, Any]]
) -> None:
    _set_focus(monkeypatch, "Dog Food")
    assert ideas_db.insert_idea({"Topic": "Uncategorised"}, brand_id="acme") is None
    assert captured_inserts == []


def test_brand_without_focus_is_unaffected(
    monkeypatch: pytest.MonkeyPatch, captured_inserts: list[dict[str, Any]]
) -> None:
    _set_focus(monkeypatch, "")
    assert ideas_db.insert_idea({"Category": "Gear", "Topic": "Best GPS"}, brand_id="acme")
    assert len(captured_inserts) == 1


def test_registry_failure_never_escapes_insert_idea(
    monkeypatch: pytest.MonkeyPatch, captured_inserts: list[dict[str, Any]]
) -> None:
    """A raising focus lookup fails CLOSED: that one idea is dropped, but the
    exception does not escape and kill the run that produced it.

    The lookup itself is defensive (see the repository test below), so this is
    the belt-and-braces path -- reached only if the delegate raises before its
    own try/except, e.g. pool construction.
    """

    def boom(_brand_id: str) -> str:
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(ideas_db.brands_db, "focus_category", boom)
    assert ideas_db.insert_idea({"Category": "Gear", "Topic": "T"}, brand_id="acme") is None
    assert captured_inserts == []


def test_focus_lookup_returns_no_focus_when_the_query_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lookup degrades to "" (no focus) rather than raising -- a registry
    hiccup must not turn into a pipeline-wide rejection of every idea."""
    from lib.brands_db.repository import BrandsRepository

    def boom(_query: str, _params: dict[str, Any]) -> dict[str, Any] | None:
        raise RuntimeError("connection reset")

    monkeypatch.setattr("lib.brands_db.repository.db.fetch_one", boom)
    assert BrandsRepository(None).focus_category("acme") == ""


def test_focus_lookup_is_empty_for_unknown_brand_and_blank_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lib.brands_db.repository import BrandsRepository

    monkeypatch.setattr("lib.brands_db.repository.db.fetch_one", lambda *_a, **_k: None)
    repo = BrandsRepository(None)
    assert repo.focus_category("nope") == ""
    assert repo.focus_category("") == ""
