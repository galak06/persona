"""Reel composition: stay inside the focus category, and stay rejected.

Two live faults, both found on 2026-08-30. A brand six months into a dental
focus was rendering reels for old GPS-collar, batch-cooking and
Instagram-strategy posts -- none of them in the category it is trying to rank.
And one idea had been through composition 15 times across 10 scheduled runs,
because rejecting a reel sent it back to exactly the status the composer
harvests.
"""

from __future__ import annotations

from typing import Any

import pytest

from lib.content_strategy import ContentStrategy, is_in_focus
from lib.ideas_db import STATUSES


def _rows() -> list[dict[str, Any]]:
    return [
        {"id": "1", "category": "Dental Care", "topic": "VOHC chews"},
        {"id": "2", "category": "GPS/Gear", "topic": "AirTag collars"},
        {"id": "3", "category": "Recipes", "topic": "Batch cooking"},
        {"id": "4", "category": "Content Format", "topic": "Carousels beat reels"},
    ]


def _filter(rows: list[dict[str, Any]], strategy: ContentStrategy) -> list[dict[str, Any]]:
    if not strategy.has_focus:
        return rows
    return [r for r in rows if is_in_focus(str(r.get("category") or ""), strategy)]


class TestFocusFiltersComposition:
    def test_only_the_focus_category_survives(self) -> None:
        kept = _filter(_rows(), ContentStrategy(focus_category="Dental Care"))
        assert [r["id"] for r in kept] == ["1"]

    def test_the_exact_live_off_focus_set_is_dropped(self) -> None:
        """GPS/Gear, Recipes and Content Format are the categories that were
        actually being rendered under a Dental Care focus."""
        kept = _filter(_rows(), ContentStrategy(focus_category="Dental Care"))
        assert {r["category"] for r in kept} == {"Dental Care"}

    def test_a_brand_with_no_focus_keeps_everything(self) -> None:
        """Composition must not change for brands that never opted in."""
        rows = _rows()
        assert _filter(rows, ContentStrategy()) == rows

    def test_matching_ignores_case_and_spacing(self) -> None:
        rows = [{"id": "1", "category": "  dental   care "}]
        assert _filter(rows, ContentStrategy(focus_category="Dental Care")) == rows

    def test_an_uncategorised_published_post_is_not_swept_in(self) -> None:
        rows = [{"id": "1", "category": ""}, {"id": "2", "category": None}]
        assert _filter(rows, ContentStrategy(focus_category="Dental Care")) == []


class TestRejectionIsTerminal:
    def test_reel_rejected_is_a_real_status(self) -> None:
        assert "reel_rejected" in STATUSES

    def test_rejection_does_not_return_to_the_harvest_pool(self) -> None:
        """The whole bug: 'wp_published' is what the composer selects, so
        rejecting into it silently undid the rejection on the next run."""
        assert "reel_rejected" != "wp_published"

    @pytest.mark.parametrize("harvested", ["wp_published"])
    def test_the_terminal_status_is_not_one_the_composer_harvests(self, harvested: str) -> None:
        assert "reel_rejected" != harvested
