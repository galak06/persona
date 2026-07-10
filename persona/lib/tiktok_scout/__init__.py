"""TikTok follow-scout library.

Public API for the TikTok scouting state management pipeline.
Scraping is handled externally by the tiktok-scout Claude skill (uses claude-in-chrome).

Typical usage::

    from lib.tiktok_scout import (
        is_north_america_likely,
        load_candidates,
        save_candidates,
        candidates_today,
        DAILY_SCOUT_CEILING,
    )
"""

from lib.tiktok_scout.candidate import TikTokCandidate
from lib.tiktok_scout.constants import (
    DAILY_SCOUT_CEILING,
    FOLLOWER_MAX,
    FOLLOWER_MIN,
    SCOUT_HASHTAGS,
)
from lib.tiktok_scout.geo_filter import is_north_america_likely
from lib.tiktok_scout.state import (
    candidates_today,
    is_already_seen,
    load_candidates,
    save_candidates,
    update_status,
)

__all__ = [
    "TikTokCandidate",
    "load_candidates",
    "is_already_seen",
    "save_candidates",
    "candidates_today",
    "update_status",
    "SCOUT_HASHTAGS",
    "DAILY_SCOUT_CEILING",
    "FOLLOWER_MIN",
    "FOLLOWER_MAX",
    "is_north_america_likely",
]
