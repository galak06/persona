"""Tests for the Phase-0 data-sufficiency gate (lib.gsc_data_sufficiency).

Pure function -- no DB, no network, no brand fixtures.
"""
# ruff: noqa: S101

from __future__ import annotations

from lib.gsc_data_sufficiency import evaluate_data_sufficiency


def _rows(n: int, *, position: float = 10.0, impressions: int = 50) -> list[tuple[str, float, int]]:
    return [(f"query {i}", position, impressions) for i in range(n)]


def test_insufficient_when_below_threshold() -> None:
    result = evaluate_data_sufficiency(_rows(14))
    assert result.sufficient is False
    assert result.qualifying_query_count == 14
    assert result.threshold == 15


def test_sufficient_at_exact_threshold() -> None:
    result = evaluate_data_sufficiency(_rows(15))
    assert result.sufficient is True
    assert result.qualifying_query_count == 15


def test_position_outside_band_does_not_qualify() -> None:
    below_band = _rows(20, position=3.0)  # position 1-5: already winning, not in the 6-25 band
    above_band = _rows(20, position=40.0)
    result = evaluate_data_sufficiency(below_band + above_band)
    assert result.qualifying_query_count == 0
    assert result.sufficient is False


def test_low_impressions_does_not_qualify() -> None:
    result = evaluate_data_sufficiency(_rows(20, impressions=39))
    assert result.qualifying_query_count == 0
    assert result.sufficient is False


def test_boundary_position_and_impressions_qualify() -> None:
    rows = [("a", 6.0, 40), ("b", 25.0, 40)]
    result = evaluate_data_sufficiency(rows, min_queries=2)
    assert result.qualifying_query_count == 2
    assert result.sufficient is True


def test_duplicate_query_text_counted_once() -> None:
    """Multiple rows for the identical query (e.g. across paginated fetches
    or several matched URLs) must count as ONE qualifying query, not one
    per row -- the gate is about distinct query coverage."""
    rows = [("same query", 10.0, 50)] * 20
    result = evaluate_data_sufficiency(rows)
    assert result.qualifying_query_count == 1
    assert result.sufficient is False


def test_custom_thresholds() -> None:
    result = evaluate_data_sufficiency(_rows(5), min_queries=5)
    assert result.sufficient is True
    assert result.threshold == 5


def test_empty_input() -> None:
    result = evaluate_data_sufficiency([])
    assert result.sufficient is False
    assert result.qualifying_query_count == 0
