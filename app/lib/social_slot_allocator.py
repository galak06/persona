"""One FB calendar shared by regular social posts and product spotlights.

Two tables now hold Facebook slots: ``content_ideas`` (regular social posts)
and ``content_derivatives`` (spotlights). Allocating from either one alone
would happily hand out a slot the *other* table already claimed, stacking two
posts on the same hour and blowing the ``facebook:page_post`` (3/day) and
``instagram:feed_post`` (2/day) rate limits the moment a brand runs both
tracks. So the allocator asks both readers and spaces off whichever slot is
later.

This lives in its own module rather than in either table's module on purpose:
``social_post_slots`` stays pure arithmetic with no DB import, and
``social_post_db`` -- already at the 300-line cap -- stays untouched and
unaware of derivatives. The three collaborators are imported as MODULES so
existing monkeypatches (`social_post_db.last_scheduled_fb_slot`, and the
`derivatives_db` seam tests fake) keep taking effect.
"""

from __future__ import annotations

from datetime import datetime

from lib import derivatives_db, social_post_db, social_post_slots


def next_shared_fb_slot(now: datetime, *, brand_id: str | None) -> datetime:
    """The FB slot a post approved at `now` should take, across both tables.

    Reads the latest claimed slot from the regular-post table and the
    derivative table, spaces off the later of the two, and falls back to a
    fresh calendar when neither has claimed anything yet.
    """
    claimed = [
        slot
        for slot in (
            social_post_db.last_scheduled_fb_slot(brand_id=brand_id),
            derivatives_db.last_scheduled_fb_slot(brand_id=brand_id),
        )
        if slot is not None
    ]
    return social_post_slots.next_free_slot(now, last_scheduled=max(claimed) if claimed else None)
