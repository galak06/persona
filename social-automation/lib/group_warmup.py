"""
Warmup gate for newly joined Facebook groups.

Facebook flags accounts that join + post too quickly. After joining a new
group, we wait before engaging:
- 48h before commenting on existing posts
- 72h before publishing our own blog-link posts

Source of truth: data/groups_tracker.json — written by fb_notification_scan.py
when a join is detected. This module is read-only.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final

GROUPS_TRACKER_PATH: Final[Path] = Path(__file__).resolve().parent.parent / "data" / "groups_tracker.json"

COMMENT_WARMUP_HOURS: Final[int] = 48
LINK_POST_WARMUP_HOURS: Final[int] = 72


def _normalize_url(url: str) -> str:
    """Strip trailing slash + lowercase for stable comparison."""
    return url.strip().rstrip("/").lower()


def _load_tracker() -> list[dict]:
    if not GROUPS_TRACKER_PATH.exists():
        return []
    try:
        with GROUPS_TRACKER_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _parse_iso(value: str) -> datetime | None:
    try:
        # tolerate trailing Z (groups_tracker writes both with and without)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def joined_at(group_url: str) -> datetime | None:
    """Return the joined_at timestamp for a group (UTC), or None if unknown."""
    target = _normalize_url(group_url)
    for entry in _load_tracker():
        if _normalize_url(entry.get("group_url", "")) == target:
            stamp = entry.get("joined_at")
            return _parse_iso(stamp) if isinstance(stamp, str) else None
    return None


def is_group_warm(group_url: str, hours: int, now: datetime | None = None) -> bool:
    """
    True if the group was joined > `hours` ago (or join date is unknown — we
    assume long-standing membership for groups that pre-date tracker entries).
    """
    joined = joined_at(group_url)
    if joined is None:
        return True
    current = now or datetime.now(timezone.utc)
    if joined.tzinfo is None:
        joined = joined.replace(tzinfo=timezone.utc)
    return current - joined >= timedelta(hours=hours)


def hours_until_warm(group_url: str, hours: int, now: datetime | None = None) -> float:
    """How many hours remain before the group clears the warmup window. 0 if warm."""
    joined = joined_at(group_url)
    if joined is None:
        return 0.0
    current = now or datetime.now(timezone.utc)
    if joined.tzinfo is None:
        joined = joined.replace(tzinfo=timezone.utc)
    elapsed = current - joined
    remaining = timedelta(hours=hours) - elapsed
    return max(0.0, remaining.total_seconds() / 3600)
