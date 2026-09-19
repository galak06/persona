"""Tests for `lib/social_slot_allocator.py` -- the shared FB calendar.

Both readers are faked at the module level: `derivatives_db.last_scheduled_fb_slot`
is a stub that raises until phase 1A lands, and neither reader may touch a live
Postgres in a unit test. Every datetime here is timezone-aware, because the two
DB readers return `timestamptz` values and comparing those to a naive datetime
raises.
"""
# ruff: noqa: S101

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lib import social_post_slots, social_slot_allocator

_NOW = datetime(2026, 8, 11, 20, tzinfo=UTC)


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    regular: datetime | None,
    derivative: datetime | None,
) -> list[str | None]:
    """Fake both readers; returns the brand_ids they were asked for."""
    seen: list[str | None] = []

    def _regular(*, brand_id: str | None = None) -> datetime | None:
        seen.append(brand_id)
        return regular

    def _derivative(*, brand_id: str | None = None) -> datetime | None:
        seen.append(brand_id)
        return derivative

    monkeypatch.setattr(social_slot_allocator.social_post_db, "last_scheduled_fb_slot", _regular)
    monkeypatch.setattr(social_slot_allocator.derivatives_db, "last_scheduled_fb_slot", _derivative)
    return seen


def test_takes_the_max_across_both_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spacing off the earlier of the two would double-book the later one."""
    _patch(
        monkeypatch,
        regular=datetime(2026, 8, 12, 13, tzinfo=UTC),
        derivative=datetime(2026, 8, 14, 13, tzinfo=UTC),
    )
    assert social_slot_allocator.next_shared_fb_slot(_NOW, brand_id="b") == datetime(
        2026, 8, 15, 13, tzinfo=UTC
    )


def test_derivative_earlier_than_regular_still_spaces_off_the_regular(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(
        monkeypatch,
        regular=datetime(2026, 8, 14, 13, tzinfo=UTC),
        derivative=datetime(2026, 8, 12, 13, tzinfo=UTC),
    )
    assert social_slot_allocator.next_shared_fb_slot(_NOW, brand_id="b") == datetime(
        2026, 8, 15, 13, tzinfo=UTC
    )


@pytest.mark.parametrize("side", ["regular", "derivative"])
def test_only_one_side_set(monkeypatch: pytest.MonkeyPatch, side: str) -> None:
    """A brand that has only ever used one track must still be spaced off it."""
    claimed = datetime(2026, 8, 14, 13, tzinfo=UTC)
    _patch(
        monkeypatch,
        regular=claimed if side == "regular" else None,
        derivative=claimed if side == "derivative" else None,
    )
    assert social_slot_allocator.next_shared_fb_slot(_NOW, brand_id="b") == datetime(
        2026, 8, 15, 13, tzinfo=UTC
    )


def test_both_none_behaves_like_a_fresh_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    """`max(())` raises -- an empty calendar must degrade to `last_scheduled=None`,
    i.e. the next preferred-hour window, not an error and not "now"."""
    _patch(monkeypatch, regular=None, derivative=None)

    slot = social_slot_allocator.next_shared_fb_slot(_NOW, brand_id="b")

    assert slot == social_post_slots.next_free_slot(_NOW, last_scheduled=None)
    assert slot == datetime(2026, 8, 12, 13, tzinfo=UTC)
    assert slot > _NOW
    assert slot.tzinfo is not None


def test_result_is_timezone_aware_and_gap_spaced(monkeypatch: pytest.MonkeyPatch) -> None:
    last = datetime(2026, 8, 20, 13, tzinfo=UTC)
    _patch(monkeypatch, regular=last, derivative=None)

    slot = social_slot_allocator.next_shared_fb_slot(_NOW, brand_id="b")

    assert slot.tzinfo is not None
    assert slot - last == timedelta(hours=social_post_slots.DEFAULT_SLOT_GAP_HOURS)


def test_brand_id_reaches_both_readers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dropping the brand on either side would space a brand off another
    brand's calendar."""
    seen = _patch(monkeypatch, regular=None, derivative=None)
    social_slot_allocator.next_shared_fb_slot(_NOW, brand_id="dogfoodandfun")
    assert seen == ["dogfoodandfun", "dogfoodandfun"]


def test_brand_id_none_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(monkeypatch, regular=None, derivative=None)
    social_slot_allocator.next_shared_fb_slot(_NOW, brand_id=None)
    assert seen == [None, None]
