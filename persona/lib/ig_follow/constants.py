"""Tunable constants for the IG follow-scout.

Kept in their own module (rather than in `__init__.py`) so consumers
can `from ig_follow.constants import X` directly — matches the
submodule-direct import convention used elsewhere in the project.
"""

from __future__ import annotations

DAILY_FOLLOW_CEILING: int = 22
"""Hard cap on follows per trailing 24h.

Middle of the 20–25/day band confirmed with the user. Trailing-window,
not calendar-day — IG's rate detector is itself trailing-window, so
matching that semantics avoids surprise blocks at midnight."""

FOLLOW_JITTER_SECONDS: tuple[int, int] = (60, 180)
"""(min, max) random sleep between follow actions.

Wider than the IG comment-jitter window (120–180s in CLAUDE.md)
because follows are more rate-sensitive than likes/comments — the
broader spread looks less mechanical to anti-spam heuristics."""
