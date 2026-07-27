"""Tests for Instagram post age parsing logic."""

from __future__ import annotations

import re

import pytest


def parse_post_age_weeks(caption: str) -> float:
    """
    Extract post age from IG caption text and convert to weeks.
    Mirrors the logic in ig_scan.py.
    """
    age_match = re.search(r"\b(\d+)(h|d|w|m)\b", caption)
    if not age_match:
        return 0.0
    val, unit = int(age_match.group(1)), age_match.group(2)
    if unit == "h":
        return val / (24 * 7)
    elif unit == "d":
        return val / 7
    elif unit == "w":
        return float(val)
    elif unit == "m":
        return val * 4.3
    return 0.0


class TestAgeParser:
    """Tests for IG post age string parsing."""

    def test_hours(self):
        assert parse_post_age_weeks("dogowner123  4h Some caption") == pytest.approx(
            4 / 168, rel=0.01
        )

    def test_days(self):
        assert parse_post_age_weeks("user  3d My post") == pytest.approx(3 / 7, rel=0.01)

    def test_weeks(self):
        assert parse_post_age_weeks("poster  2w Great content") == 2.0

    def test_months(self):
        assert parse_post_age_weeks("user  6m Old post") == pytest.approx(6 * 4.3, rel=0.01)

    def test_large_weeks(self):
        assert parse_post_age_weeks("user  101w Ancient post") == 101.0

    def test_504_weeks(self):
        assert parse_post_age_weeks("user  504w Really old") == 504.0

    def test_no_age_returns_zero(self):
        assert parse_post_age_weeks("Just a normal caption with no age") == 0.0

    def test_1_hour(self):
        assert parse_post_age_weeks("user  1h Just posted") == pytest.approx(1 / 168, rel=0.01)

    def test_14_days(self):
        result = parse_post_age_weeks("user  14d Two weeks ago")
        assert result == pytest.approx(2.0, rel=0.01)

    def test_age_filter_2_week_cutoff(self):
        """Posts older than 2 weeks should be filtered out."""
        fresh = parse_post_age_weeks("user  1d Fresh post")
        borderline = parse_post_age_weeks("user  2w Exactly 2 weeks")
        old = parse_post_age_weeks("user  3w Too old")
        ancient = parse_post_age_weeks("user  101w Way too old")

        assert fresh < 2.0, "1 day old should pass"
        assert borderline <= 2.0, "Exactly 2 weeks should pass"
        assert old > 2.0, "3 weeks should be filtered"
        assert ancient > 2.0, "101 weeks should be filtered"

    def test_age_embedded_in_text(self):
        """Age marker can appear anywhere in the caption."""
        assert parse_post_age_weeks("Check this out 5d ago") == pytest.approx(5 / 7, rel=0.01)

    def test_multiple_age_markers_takes_first(self):
        result = parse_post_age_weeks("user  2w updated 1d ago")
        assert result == 2.0  # takes first match

    def test_digits_without_unit_ignored(self):
        assert parse_post_age_weeks("I have 5 dogs") == 0.0
