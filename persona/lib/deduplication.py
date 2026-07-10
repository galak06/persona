"""
Deduplication cache for DogFoodAndFun social media automation.
Tracks post IDs already engaged with, with a 60-day rolling TTL.
Persists state to .claude/state/dedup_cache.json
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

Platform = Literal["facebook", "instagram", "wordpress"]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = _PROJECT_ROOT / ".claude" / "state" / "dedup_cache.json"
TTL_DAYS = 60


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            with CACHE_FILE.open() as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Cache not a dict")
            return data
        except Exception:
            # Corrupted — reset and continue
            CACHE_FILE.write_text("{}")
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_FILE.open("w") as f:
        json.dump(cache, f, indent=2)


def _purge_expired(cache: dict) -> dict:
    """Remove entries older than TTL_DAYS. Modifies cache in-place and returns it."""
    cutoff = (date.today() - timedelta(days=TTL_DAYS)).isoformat()
    for platform in list(cache.keys()):
        for post_id in list(cache[platform].keys()):
            entry = cache[platform][post_id]
            if entry.get("engaged_at", "9999") < cutoff:
                del cache[platform][post_id]
        if not cache[platform]:
            del cache[platform]
    return cache


def is_duplicate(platform: Platform, post_id: str) -> bool:
    """Returns True if this post has already been engaged with in the last 60 days.

    Presence-only: True for ANY prior interaction (queued, liked, commented). Use
    this at scan time to avoid re-processing a post. For "did we already COMMENT
    here?" use :func:`already_commented` — a liked-or-queued post is not yet
    commented, so the commenter must not treat it as a duplicate.
    """
    cache = _load_cache()
    cache = _purge_expired(cache)
    return post_id in cache.get(platform, {})


def already_commented(platform: Platform, post_id: str) -> bool:
    """Returns True only if we have SUCCESSFULLY commented on this post before.

    Distinct from :func:`is_duplicate`: the scanner pre-marks every queued post
    (``action="comment_queued"``) and liked post (``action="like"``), so a plain
    presence check would make the commenter skip everything it just queued. Here
    we match only a recorded ``comment`` engagement (``status="engaged"``).
    """
    cache = _load_cache()
    cache = _purge_expired(cache)
    entry = cache.get(platform, {}).get(post_id)
    if not entry:
        return False
    return entry.get("action") == "comment" and entry.get("status") == "engaged"


def mark_engaged(
    platform: Platform,
    post_id: str,
    action: str,
    group_or_hashtag: str = "",
    status: str = "engaged",
) -> None:
    """
    Record that a post has been engaged with.
    status: "engaged" | "FAILED" | "skipped"
    """
    cache = _load_cache()
    cache = _purge_expired(cache)

    if platform not in cache:
        cache[platform] = {}

    cache[platform][post_id] = {
        "engaged_at": date.today().isoformat(),
        "action": action,
        "group_or_hashtag": group_or_hashtag,
        "status": status,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    _save_cache(cache)


def get_cache_stats() -> dict:
    """Returns count of cached entries per platform."""
    cache = _load_cache()
    cache = _purge_expired(cache)
    return {platform: len(posts) for platform, posts in cache.items()}


def print_stats() -> None:
    stats = get_cache_stats()
    print("\n=== Dedup Cache Stats ===")
    for platform, count in stats.items():
        print(f"  {platform:<15} {count} posts cached (last {TTL_DAYS} days)")
    if not stats:
        print("  Cache is empty.")
    print()


if __name__ == "__main__":
    print_stats()
