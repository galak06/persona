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

import contextlib
import json
import os
from collections.abc import Generator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from lib.io.jsonio import locked_json
from lib.observability import get_logger

logger = get_logger(__name__)

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


def _quarantine(cache_file: Path, reason: str) -> None:
    """Rename an unusable cache aside instead of destroying it.

    This module used to answer any parse failure with
    ``cache_file.write_text("{}")`` — one torn read and 60 days of like and
    comment history for BOTH platforms were gone, silently, with nothing left
    to recover from. A cache we cannot parse is a cache we cannot trust, but
    it is still data: move it to a ``.corrupt-<timestamp>`` sibling, log loudly,
    and let the caller start from an empty one. The rename is atomic, so a
    concurrent reader sees the old file or no file, never a half-moved one.
    """
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
    quarantined = cache_file.with_name(f"{cache_file.name}.corrupt-{stamp}")
    try:
        os.replace(cache_file, quarantined)
    except OSError as exc:  # pragma: no cover - filesystem failure
        logger.error(
            "dedup_cache_quarantine_failed",
            path=str(cache_file),
            reason=reason,
            error=str(exc),
        )
        return
    logger.warning(
        "dedup_cache_quarantined",
        path=str(cache_file),
        quarantined=str(quarantined),
        reason=reason,
    )


def _read_valid_cache(cache_file: Path) -> dict | None:
    """Parse `cache_file`, quarantining it if it is unreadable or malformed.

    Returns None when there is nothing usable to read — missing, empty, or
    just quarantined. An empty file is not quarantined: it holds no history to
    preserve, and `locked_json` seeds it on the next write.
    """
    if not cache_file.exists():
        return None
    try:
        raw = cache_file.read_text(encoding="utf-8")
    except OSError as exc:
        _quarantine(cache_file, f"unreadable: {exc}")
        return None
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _quarantine(cache_file, f"invalid JSON: {exc}")
        return None
    if not isinstance(data, dict):
        _quarantine(cache_file, f"top level is {type(data).__name__}, expected object")
        return None
    return data


def _load_cache() -> dict:
    """Read-only snapshot of the cache. Never writes to disk."""
    return _read_valid_cache(_cache_path()) or {}


@contextlib.contextmanager
def _locked_cache() -> Generator[dict, None, None]:
    """Yield the purged cache for a read-modify-write held under an OS lock.

    `locked_json` flocks the file for the whole block and writes the result
    back atomically (temp file + `os.replace`), so two processes marking two
    different posts cannot lose each other's entry, and no reader — including
    a crash-interrupted one — can observe a half-written cache.
    """
    cache_file = _cache_path()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    # Quarantine BEFORE taking the lock: `locked_json` falls back to its
    # default on a parse failure and would write that default back, which is
    # the very self-wipe this module is getting rid of.
    _read_valid_cache(cache_file)
    empty: dict = {}
    with locked_json(cache_file, empty) as cache:
        _purge_expired(cache)
        yield cache


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
    with _locked_cache() as cache:
        if platform not in cache:
            cache[platform] = {}

        cache[platform][post_id] = {
            "engaged_at": date.today().isoformat(),
            "action": action,
            "group_or_hashtag": group_or_hashtag,
            "status": status,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }


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
