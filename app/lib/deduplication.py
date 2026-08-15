"""Deduplication cache: post IDs already engaged with, 60-day rolling TTL.

Persists to ``<BRAND_DIR>/state/dedup_cache.json`` — the brand's own state
directory, resolved through :mod:`lib.brand_context`.

It used to persist to an engine-relative ``app/.claude/state/dedup_cache.json``,
which was wrong three ways at once:

* **It was ephemeral in production.** ``docker-compose.yml`` mounts only
  ``brands/`` into the worker, so ``/app/.claude/`` lived in the container's
  writable layer. Every rebuild silently reset the like/comment history, and
  the file did not exist in the running container at all.
* **It was invisible to the dashboard.** ``scripts/status.py`` reads
  ``settings.paths.dedup_cache`` — the brand-scoped path — so it reported on a
  file this module had stopped writing in June 2026.
* **It was shared across brands.** One engine-level cache for every brand,
  keyed only by platform and post id.

``scripts/migrate_dedup_cache.py`` merges the old engine-level file forward.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

Platform = Literal["facebook", "instagram", "wordpress"]

# Set to a Path to override the location (tests do this). Left as None, the
# path follows the active brand, resolved on each call so a mid-process brand
# change is honoured rather than baked in at import.
CACHE_FILE: Path | None = None
TTL_DAYS = 60


def _cache_path() -> Path:
    if CACHE_FILE is not None:
        return CACHE_FILE
    from lib.config import settings

    paths = settings.paths
    if paths is None:  # pragma: no cover - load_config always sets it
        raise RuntimeError("settings.paths is unset; lib.config failed to resolve BRAND_DIR")
    return paths.dedup_cache


def _load_cache() -> dict:
    cache_file = _cache_path()
    if cache_file.exists():
        try:
            with cache_file.open() as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Cache not a dict")
            return data
        except Exception:
            # Corrupted — reset and continue
            cache_file.write_text("{}")
    return {}


def _save_cache(cache: dict) -> None:
    cache_file = _cache_path()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with cache_file.open("w") as f:
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
