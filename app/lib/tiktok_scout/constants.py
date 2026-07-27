"""Tunable constants for the TikTok follow-scout.

Kept in their own module so consumers can
`from lib.tiktok_scout.constants import X` directly — matches the
submodule-direct import convention used elsewhere in the project.
"""

from __future__ import annotations

SCOUT_HASHTAGS: list[str] = [
    "dogfood",
    "homemadedogfood",
    "dogrecipes",
    "doglifestyle",
    "petnutrition",
    "rawdogfood",
    "dogmom",
    "dogdad",
    "healthydogfood",
]
"""Hashtag pages the scout rotates through when discovering creators."""

DAILY_SCOUT_CEILING: int = 50
"""Hard cap on candidates discovered per trailing 24 h.

Keeps the follow queue from ballooning while TikTok's rate signals are
still being calibrated."""

FOLLOWER_MIN: int = 1_000
"""Skip accounts below this follower count — too small to indicate real reach."""

FOLLOWER_MAX: int = 500_000
"""Skip accounts above this follower count — likely celebrity/brand, not peer."""

JITTER_SECONDS: tuple[int, int] = (3, 8)
"""(min, max) random sleep between scroll actions inside scout_hashtag.

Short jitter is acceptable at the scroll level (not the follow level).
Follow-action jitter is enforced separately by the worker."""
