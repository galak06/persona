# pyright: reportMissingImports=false
"""Unit tests for pipeline.seasons (pure season domain logic). No I/O."""
# ruff: noqa: S101

from __future__ import annotations

from datetime import date

import pytest
from pipeline import seasons


def test_current_season_by_month() -> None:
    assert seasons.current_season(date(2026, 1, 15)) == seasons.WINTER
    assert seasons.current_season(date(2026, 4, 1)) == seasons.SPRING
    assert seasons.current_season(date(2026, 7, 20)) == seasons.SUMMER
    assert seasons.current_season(date(2026, 10, 3)) == seasons.FALL
    assert seasons.current_season(date(2026, 12, 25)) == seasons.WINTER


def test_normalize_season_trims_and_lowercases() -> None:
    assert seasons.normalize_season("  Fall ") == seasons.FALL


def test_normalize_season_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown season"):
        seasons.normalize_season("monsoon")


def test_infer_seasons_keyword_signal() -> None:
    assert seasons.FALL in seasons.infer_seasons(
        "Pumpkin Spice Pup Treats", ["pumpkin"]
    )
    assert seasons.SUMMER in seasons.infer_seasons(
        "Frozen Watermelon Pupsicles", []
    )
    assert seasons.WINTER in seasons.infer_seasons("Gingerbread Dog Cookies", [])


def test_infer_seasons_no_signal_is_all_season() -> None:
    assert seasons.infer_seasons("Chicken & Rice Biscuits", ["chicken"]) == []


def test_in_season_predicate() -> None:
    assert seasons.in_season([], seasons.SUMMER) is True  # all-season
    assert seasons.in_season([seasons.FALL], seasons.FALL) is True
    assert seasons.in_season([seasons.FALL], seasons.SUMMER) is False
