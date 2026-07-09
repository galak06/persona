"""History I/O and rate-window accounting for the IG follow-scout.

State file: `.claude/state/ig_follow_history.json`. One entry per
successful follow, append-only modulo the periodic unfollow-sweep (a
Stage 4 concern — for now `unfollowed_at` stays None forever).

History entry shape (JSON):

    {
        "handle":          "<lowercase, no @>",
        "source_handle":   "<competitor handle we found them via>",
        "source_signal":   "follower" | "engager",
        "followed_at":     "<ISO-8601 UTC, trailing Z>",
        "unfollowed_at":   null
    }

All writes go through `lib.io.jsonio.write_json` so a crash mid-write
can't corrupt history. Reads tolerate the file being absent (first run).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict, cast

from lib.io.jsonio import read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HISTORY_FILE = PROJECT_ROOT / ".claude/state/ig_follow_history.json"


class FollowRecord(TypedDict):
    """One row in `ig_follow_history.json`."""

    handle: str
    source_handle: str
    source_signal: str
    followed_at: str
    unfollowed_at: str | None


def _now_iso_z() -> str:
    """UTC now in ISO 8601 with trailing Z."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def load_history() -> list[FollowRecord]:
    """Return all follow records, oldest-first. Empty list if file missing."""
    raw = read_json(HISTORY_FILE, default=[])
    if not isinstance(raw, list):
        return []
    return cast("list[FollowRecord]", raw)


def _save_history(history: list[FollowRecord]) -> None:
    write_json(HISTORY_FILE, history)


def is_already_followed(handle: str, history: list[FollowRecord] | None = None) -> bool:
    """True if `handle` appears in history with `unfollowed_at` still None.

    A re-follow after a prior unfollow is allowed in principle but
    callers should avoid it — IG's anti-spam treats follow-unfollow
    loops as one of the strongest negative signals.
    """
    needle = handle.lower().lstrip("@")
    records = history if history is not None else load_history()
    return any(r["handle"] == needle and r.get("unfollowed_at") is None for r in records)


def follows_in_window(window: timedelta, history: list[FollowRecord] | None = None) -> int:
    """Count follows whose `followed_at` falls inside the trailing `window`."""
    cutoff = datetime.now(UTC) - window
    records = history if history is not None else load_history()
    n = 0
    for r in records:
        try:
            ts = datetime.fromisoformat(r["followed_at"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts >= cutoff:
            n += 1
    return n


def follows_today(history: list[FollowRecord] | None = None) -> int:
    """Number of follows whose `followed_at` falls inside the last 24h.

    Trailing 24h, not "calendar day," because the daily ceiling matters
    for IG's rate detector (which is itself trailing-window), not for
    human accounting.
    """
    return follows_in_window(timedelta(hours=24), history)


def record_follow(
    handle: str,
    source_handle: str,
    source_signal: str,
) -> FollowRecord:
    """Append a follow record and persist atomically.

    Returns the persisted record. Idempotent on `handle`: if the handle
    already has an active follow record, that record is returned and no
    write happens.

    Args:
        handle: Followed user's IG username (no @, any case).
        source_handle: Competitor we found them through.
        source_signal: "follower" or "engager". Not validated here so
            this module stays free of the candidate-side Literal type;
            scout code guarantees the value.
    """
    needle = handle.lower().lstrip("@")
    history = load_history()
    for r in history:
        if r["handle"] == needle and r.get("unfollowed_at") is None:
            return r

    record: FollowRecord = {
        "handle": needle,
        "source_handle": source_handle.lower().lstrip("@"),
        "source_signal": source_signal,
        "followed_at": _now_iso_z(),
        "unfollowed_at": None,
    }
    history.append(record)
    _save_history(history)
    return record
