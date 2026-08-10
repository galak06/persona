"""Slot arithmetic for spacing social posts across the week.

Pure functions, no DB and no clock of their own -- ``now`` is always passed in,
so every case here is directly testable and the caller decides whose clock is
authoritative.

Why posts are scheduled rather than published on approval: approving a batch
of five and having all five go out inside a minute reads as inorganic to both
the audience and Meta's ranking, and would breach ``facebook:page_post`` (3/day)
outright. Each approval instead claims the next free slot, so a batch spreads
itself over the following days without the reviewer having to time anything.

All times are UTC -- the codebase carries no timezone config. The default
posting hour is chosen for the brand's stated market (USA + Canada):
13:00 UTC is 09:00 US Eastern / 06:00 Pacific in summer, inside the 8-10am
audience-local morning window ``app/CLAUDE.md`` documents for FB Page posts.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# One post per day, in the morning-ET window. Both are overridable per call.
DEFAULT_SLOT_GAP_HOURS = 24.0
DEFAULT_PREFERRED_HOUR_UTC = 13


def next_preferred_slot(
    now: datetime, *, preferred_hour_utc: int = DEFAULT_PREFERRED_HOUR_UTC
) -> datetime:
    """The next occurrence of `preferred_hour_utc` strictly after `now`.

    Used as the FLOOR for a newly scheduled post -- never "right now", so an
    approval late at night still lands in the next morning's window rather
    than going out at 3am.
    """
    candidate = now.replace(hour=preferred_hour_utc, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def next_free_slot(
    now: datetime,
    *,
    last_scheduled: datetime | None,
    slot_gap_hours: float = DEFAULT_SLOT_GAP_HOURS,
    preferred_hour_utc: int = DEFAULT_PREFERRED_HOUR_UTC,
) -> datetime:
    """The slot a post approved at `now` should take.

    `last_scheduled` is the latest slot already claimed by this brand (None if
    none). The answer is whichever is later: the next preferred-hour window, or
    one full gap after the last claimed slot. That keeps an approved batch
    spaced by `slot_gap_hours` while never scheduling into the past.
    """
    floor = next_preferred_slot(now, preferred_hour_utc=preferred_hour_utc)
    if last_scheduled is None:
        return floor
    return max(floor, last_scheduled + timedelta(hours=slot_gap_hours))
