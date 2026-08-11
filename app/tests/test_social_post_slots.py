"""Tests for `lib.social_post_slots` -- the week-spreading slot arithmetic.

Pure functions with `now` injected, so no clock mocking and no DB.
"""
# ruff: noqa: S101

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

from lib.social_post_slots import (
    DEFAULT_PREFERRED_HOUR_UTC,
    DEFAULT_SLOT_GAP_HOURS,
    next_free_slot,
    next_preferred_slot,
)


def _utc(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


# ── next_preferred_slot ───────────────────────────────────────────────────


def test_morning_approval_lands_same_day() -> None:
    slot = next_preferred_slot(_utc(2026, 8, 10, 9), preferred_hour_utc=13)
    assert slot == _utc(2026, 8, 10, 13)


def test_evening_approval_rolls_to_next_day() -> None:
    """A late approval must not fire at 3am -- it waits for the window."""
    slot = next_preferred_slot(_utc(2026, 8, 10, 22), preferred_hour_utc=13)
    assert slot == _utc(2026, 8, 11, 13)


def test_exactly_at_the_hour_rolls_forward() -> None:
    """`<=` not `<`: scheduling for 'right now' would publish instantly,
    defeating the point of a slot."""
    slot = next_preferred_slot(_utc(2026, 8, 10, 13), preferred_hour_utc=13)
    assert slot == _utc(2026, 8, 11, 13)


def test_default_hour_is_us_morning() -> None:
    """13:00 UTC = 09:00 US Eastern in summer, the documented FB morning
    window for a USA+Canada audience."""
    assert DEFAULT_PREFERRED_HOUR_UTC == 13


# ── next_free_slot ────────────────────────────────────────────────────────


def test_first_post_takes_the_next_window() -> None:
    slot = next_free_slot(_utc(2026, 8, 10, 9), last_scheduled=None, preferred_hour_utc=13)
    assert slot == _utc(2026, 8, 10, 13)


def test_second_post_lands_a_full_gap_later() -> None:
    slot = next_free_slot(
        _utc(2026, 8, 10, 9),
        last_scheduled=_utc(2026, 8, 10, 13),
        slot_gap_hours=24.0,
        preferred_hour_utc=13,
    )
    assert slot == _utc(2026, 8, 11, 13)


def test_batch_of_five_spreads_over_five_days() -> None:
    """The scenario this exists for: approving five posts in one sitting must
    not dump them together -- one per day, in the morning window."""
    now = _utc(2026, 8, 10, 9)
    last: datetime | None = None
    slots: list[datetime] = []
    for _ in range(5):
        last = next_free_slot(now, last_scheduled=last, preferred_hour_utc=13)
        slots.append(last)

    assert slots == [_utc(2026, 8, d, 13) for d in (10, 11, 12, 13, 14)]
    assert all(b - a == timedelta(hours=24) for a, b in pairwise(slots))
    # Meta's weekly card closes Aug 15 -- all five land before it.
    assert slots[-1] < _utc(2026, 8, 15)


def test_stale_last_scheduled_does_not_schedule_into_the_past() -> None:
    """A slot claimed days ago must not pull the next one backwards."""
    slot = next_free_slot(
        _utc(2026, 8, 10, 9),
        last_scheduled=_utc(2026, 8, 1, 13),
        preferred_hour_utc=13,
    )
    assert slot == _utc(2026, 8, 10, 13)


def test_custom_gap_allows_denser_cadence() -> None:
    slot = next_free_slot(
        _utc(2026, 8, 10, 9),
        last_scheduled=_utc(2026, 8, 10, 13),
        slot_gap_hours=8.0,
        preferred_hour_utc=13,
    )
    assert slot == _utc(2026, 8, 10, 21)


def test_default_gap_is_one_day() -> None:
    assert DEFAULT_SLOT_GAP_HOURS == 24.0
