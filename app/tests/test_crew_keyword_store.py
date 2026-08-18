"""Unit tests for lib.crew.keyword_store.

The store is what turns the scout's frozen vocabulary into a growing one:
`lib.crew.trends` already researches the market every run, and this keeps
what it found so the next run searches wider. These tests pin the two
properties that make that safe -- the store is additive, and what reaches a
prompt is bounded.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from lib.crew import keyword_store
from lib.crew.trends.models import TrendSignal

_T0 = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
_T1 = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)


def _signal(keyword: str, *, score: float = 50.0, category: str = "Health") -> TrendSignal:
    return TrendSignal(
        keyword=keyword,
        category=category,
        opportunity_type="web_discovery",
        score=score,
        reason="grounded in a search finding",
    )


def test_records_new_keywords_and_reports_counts(tmp_path: Path) -> None:
    new, updated = keyword_store.record_signals(
        tmp_path, [_signal("raw feeding transition"), _signal("dog joint supplements")], now=_T0
    )

    assert (new, updated) == (2, 0)
    assert set(keyword_store.load(tmp_path)) == {
        "raw feeding transition",
        "dog joint supplements",
    }


def test_second_sighting_updates_without_losing_first_seen(tmp_path: Path) -> None:
    """The additive contract: an entry is updated, never rewritten. A term
    that trends again must not lose when it was first discovered."""
    keyword_store.record_signals(tmp_path, [_signal("raw feeding", score=40.0)], now=_T0)
    new, updated = keyword_store.record_signals(
        tmp_path, [_signal("raw feeding", score=90.0)], now=_T1
    )

    entry = keyword_store.load(tmp_path)["raw feeding"]
    assert (new, updated) == (0, 1)
    assert entry["first_seen"] == _T0.isoformat()
    assert entry["last_seen"] == _T1.isoformat()
    assert entry["times_seen"] == 2


def test_best_score_only_moves_up(tmp_path: Path) -> None:
    """One weak sighting must not demote a term that ranked strongly before,
    or a single bad run would push good vocabulary out of `active_seeds`."""
    keyword_store.record_signals(tmp_path, [_signal("canicross gear", score=95.0)], now=_T0)
    keyword_store.record_signals(tmp_path, [_signal("canicross gear", score=10.0)], now=_T1)

    assert keyword_store.load(tmp_path)["canicross gear"]["best_score"] == 95.0


def test_nothing_is_ever_removed_by_a_later_run(tmp_path: Path) -> None:
    """A term that stops trending stays on file. This is the property that
    makes the store safe to feed back into search vocabulary."""
    keyword_store.record_signals(tmp_path, [_signal("seasonal allergies")], now=_T0)
    keyword_store.record_signals(tmp_path, [_signal("something else entirely")], now=_T1)

    assert "seasonal allergies" in keyword_store.load(tmp_path)


def test_keywords_are_matched_case_and_whitespace_insensitively(tmp_path: Path) -> None:
    keyword_store.record_signals(tmp_path, [_signal("Raw Feeding")], now=_T0)
    keyword_store.record_signals(tmp_path, [_signal("  raw feeding  ")], now=_T1)

    assert len(keyword_store.load(tmp_path)) == 1


def test_active_seeds_ranks_by_score_and_bounds_the_slice(tmp_path: Path) -> None:
    """The store grows forever by design, so what reaches an agent's context
    is capped -- otherwise a year of discoveries crowds out the trend signals
    the prompt is actually about."""
    keyword_store.record_signals(
        tmp_path,
        [_signal(f"kw{i}", score=float(i)) for i in range(10)],
        now=_T0,
    )

    seeds = keyword_store.active_seeds(tmp_path, limit=3)

    assert [s.keyword for s in seeds] == ["kw9", "kw8", "kw7"]
    assert all(s.source == keyword_store.DISCOVERED_SOURCE for s in seeds)


def test_active_seeds_skips_terms_the_operator_already_curated(tmp_path: Path) -> None:
    """A curated term must not occupy one of the limited slots -- it is
    already in the vocabulary via `content_analysis.keywords`."""
    keyword_store.record_signals(
        tmp_path, [_signal("dog food", score=99.0), _signal("novel term", score=1.0)], now=_T0
    )

    seeds = keyword_store.active_seeds(tmp_path, limit=5, exclude={"dog food"})

    assert [s.keyword for s in seeds] == ["novel term"]


def test_empty_signal_list_is_a_no_op(tmp_path: Path) -> None:
    assert keyword_store.record_signals(tmp_path, [], now=_T0) == (0, 0)
    assert not keyword_store.store_path(tmp_path).exists()


def test_load_of_a_corrupt_store_degrades_to_the_curated_vocabulary(tmp_path: Path) -> None:
    """Never crash the run: a damaged store costs breadth, not the scout."""
    path = keyword_store.store_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")

    assert keyword_store.load(tmp_path) == {}
    assert keyword_store.active_seeds(tmp_path) == []


def test_store_is_written_as_readable_json_with_provenance(tmp_path: Path) -> None:
    """The file is meant to be inspectable by the operator -- that is half the
    reason discoveries live here rather than inside config.json."""
    keyword_store.record_signals(tmp_path, [_signal("gut health toppers", score=72.5)], now=_T0)

    data = json.loads(keyword_store.store_path(tmp_path).read_text(encoding="utf-8"))
    entry = data["keywords"]["gut health toppers"]

    assert data["version"] == 1
    assert entry["keyword"] == "gut health toppers"
    assert entry["category"] == "Health"
    assert entry["best_score"] == 72.5
    assert entry["reason"] == "grounded in a search finding"
