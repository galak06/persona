"""Pure parsing helpers for InstagramHashtagAdapter.

Absorbed from the deleted IG-like-helpers module during the OutboundEngagement
refactor. Kept here (rather than inline in instagram.py) so the adapter
module stays under the 300-line cap.
"""

from __future__ import annotations

import re
from datetime import date


def parse_like_count(text: str) -> int:
    """Parse '1,234 likes' / '12.5K likes' / '1.2M likes' into an int."""
    if not text:
        return 0
    t = text.lower().replace(",", "")
    m = re.search(r"(\d+\.?\d*)\s*k", t)
    if m:
        return int(float(m.group(1)) * 1000)
    m = re.search(r"(\d+\.?\d*)\s*m", t)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"(\d+)", t)
    return int(m.group(1)) if m else 0


def parse_comment_count(text: str) -> int:
    """Parse 'View all 42 comments' into 42."""
    if not text:
        return 0
    m = re.search(r"(\d+)\s*comment", text.lower())
    return int(m.group(1)) if m else 0


def parse_post_age_weeks(caption: str) -> float:
    """Extract post age in weeks from an IG caption fragment like '4h', '3d', '2w', '4m'."""
    m = re.search(r"\b(\d+)(h|d|w|m)\b", caption)
    if not m:
        return 0.0
    val, unit = int(m.group(1)), m.group(2)
    if unit == "h":
        return val / (24 * 7)
    if unit == "d":
        return val / 7
    if unit == "w":
        return float(val)
    return val * 4.3  # months


def parse_author_from_caption(caption: str) -> str:
    """Fallback author parser: IG captions often start with 'username  3w Caption...'."""
    if not caption:
        return ""
    m = re.match(r"^([a-zA-Z0-9_.]+)\s", caption)
    return m.group(1).lower() if m else ""


def should_scan_today(freq: str, today: date) -> bool:
    """Whether a CSV row with `scan_frequency=<freq>` is in scope today.

    Verbatim cadence rules from scripts/ig_scan.py:79-86.
    """
    if freq == "daily":
        return True
    if freq == "every_2_days":
        return today.toordinal() % 2 == 0
    if freq == "weekly":
        return today.weekday() == 0  # Mondays
    return False
