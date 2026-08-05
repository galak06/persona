"""Candidate state I/O for the TikTok follow-scout.

State file: `{BRAND_DIR}/data/trackers/tiktok_follow_candidates.json`.
One entry per discovered candidate, append-only by handle. Status is
updated in-place as the candidate moves through the pipeline.

Entry shape (JSON):

    {
        "handle":          "<lowercase, no @>",
        "display_name":    "<display name or handle>",
        "bio":             "<bio text or empty string>",
        "follower_count":  0,
        "source_hashtag":  "<hashtag>",
        "discovered_at":   "<ISO-8601 UTC, trailing Z>",
        "status":          "pending" | "followed" | "skipped"
    }

All writes go through `lib.io.jsonio.write_json` so a crash mid-write
cannot corrupt state. Reads tolerate the file being absent (first run).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from lib.io.jsonio import read_json, write_json
from lib.tiktok_scout.candidate import TikTokCandidate


def _state_path() -> Path:
    """Resolve the state file path from BRAND_DIR env var."""
    brand_dir = os.environ["BRAND_DIR"]
    return Path(brand_dir) / "data" / "trackers" / "tiktok_follow_candidates.json"


def load_candidates() -> list[dict[str, object]]:
    """Return all candidate records, oldest-first. Empty list if file missing."""
    raw = read_json(_state_path(), default=[])
    if not isinstance(raw, list):
        return []
    return cast("list[dict[str, object]]", raw)


def is_already_seen(handle: str) -> bool:
    """True if `handle` already appears in state (any status).

    Prevents re-discovering and re-queueing the same creator across runs.

    Args:
        handle: TikTok username without leading @. Case-insensitive.
    """
    needle = handle.lower().lstrip("@")
    return any(
        str(r.get("handle", "")).lower() == needle
        for r in load_candidates()
    )


def save_candidates(candidates: list[TikTokCandidate]) -> None:
    """Persist new candidates, merging with existing records by handle.

    Append-only semantics: existing records are never removed. If a
    candidate handle is already in state the existing record is preserved
    unchanged (first-discovery wins).

    Args:
        candidates: New candidates returned by the scout. May overlap with
            already-seen handles — duplicates are silently dropped.
    """
    existing = load_candidates()
    existing_handles: set[str] = {
        str(r.get("handle", "")).lower() for r in existing
    }

    merged = list(existing)
    for c in candidates:
        key = c.handle.lower().lstrip("@")
        if key not in existing_handles:
            merged.append(c.to_dict())
            existing_handles.add(key)

    write_json(_state_path(), merged)


def candidates_today() -> int:
    """Count candidates whose `discovered_at` date matches today UTC.

    Uses calendar-day comparison (UTC date) rather than a trailing window
    because the daily ceiling is reported to the user as a day-bucket figure.
    """
    today = datetime.now(UTC).date().isoformat()
    count = 0
    for r in load_candidates():
        ts_raw = str(r.get("discovered_at", ""))
        # discovered_at is ISO-8601; date portion is the first 10 chars
        if ts_raw[:10] == today:
            count += 1
    return count


def update_status(handle: str, status: str) -> None:
    """Find candidate by handle and update its status field in-place.

    Writes back atomically. No-op if the handle is not found (so callers
    don't need to guard against missing entries).

    Args:
        handle: TikTok username without leading @. Case-insensitive.
        status: New status value. Callers should use "followed" or "skipped".
    """
    needle = handle.lower().lstrip("@")
    records = load_candidates()
    updated = False
    for r in records:
        if str(r.get("handle", "")).lower() == needle:
            r["status"] = status
            updated = True
            break

    if updated:
        write_json(_state_path(), records)
