# pyright: reportMissingImports=false
"""Tail-N reader for ``logs/engagement_log.jsonl`` used by ``GET /activity``.

The full file is small today (~100 lines) but will grow unbounded — every
comment, like, group post, group join, page post, and own-reply append a
row. The HTTP poll cadence is 5s from the web UI, so we cache the parsed
tail keyed on ``(mtime, st_size)`` and invalidate whenever the file
changes. The first poll after a writer appends pays the parse cost; every
subsequent poll until the next append is O(1).

Action normalisation: legacy rows use ``"group_join_request"`` while the
spec calls the canonical action ``"group_join"``. We normalise inbound so
the frontend literal type only ever sees ``group_join``. Unknown actions
are dropped (logged once per occurrence) rather than crashing the feed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
ENGAGEMENT_LOG_PATH: Path = _REPO_ROOT / "logs" / "engagement_log.jsonl"

__all__ = ["ENGAGEMENT_LOG_PATH", "VALID_ACTIONS", "read_recent"]

# Must mirror ``api.schemas.ActionLiteral``. Listed here so the reader
# can drop unknown rows cheaply without importing pydantic.
VALID_ACTIONS: frozenset[str] = frozenset(
    {
        "comment",
        "like",
        "group_post",
        "reply",
        "own_reply",
        "page_post",
        "feed_post",
        "group_join",
    }
)
_LEGACY_ACTION_ALIASES: dict[str, str] = {"group_join_request": "group_join"}

_log = logging.getLogger(__name__)

# Cache key: (mtime_ns, st_size). Value: parsed list[dict] in file order.
# A single global cache is fine — this module is process-local and the
# FastAPI workers don't share state. Rebuild on any mtime/size change.
_CACHE_KEY: tuple[int, int] | None = None
_CACHE_ENTRIES: list[dict[str, Any]] = []
_CACHE_TOTAL: int = 0


def _stat_key() -> tuple[int, int] | None:
    """Cache key for the current file state. ``None`` if file missing."""
    try:
        st = ENGAGEMENT_LOG_PATH.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _reload() -> None:
    """Re-parse the entire JSONL file into the module-level cache.

    For a file that grows past ~10k lines we'd want a true tail-from-end
    seek, but the current footprint (88 lines on 2026-05-15) makes the
    full read cheap and keeps the code obvious. Revisit if the file
    crosses ~50k rows.
    """
    global _CACHE_ENTRIES, _CACHE_TOTAL
    entries: list[dict[str, Any]] = []
    total = 0
    if not ENGAGEMENT_LOG_PATH.exists():
        _CACHE_ENTRIES = []
        _CACHE_TOTAL = 0
        return
    with ENGAGEMENT_LOG_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            action = entry.get("action")
            if isinstance(action, str) and action in _LEGACY_ACTION_ALIASES:
                entry["action"] = _LEGACY_ACTION_ALIASES[action]
            if entry.get("action") not in VALID_ACTIONS:
                continue
            # Backfill ``date`` from ``timestamp`` for older rows that
            # only have the latter. ``ActivityEntry`` requires both.
            if "date" not in entry:
                ts = entry.get("timestamp")
                if isinstance(ts, str) and len(ts) >= 10:
                    entry["date"] = ts[:10]
            entries.append(entry)
    _CACHE_ENTRIES = entries
    _CACHE_TOTAL = total


def _ensure_cache() -> None:
    """Refresh the cache iff the underlying file mtime/size has changed."""
    global _CACHE_KEY
    key = _stat_key()
    if key is None:
        _CACHE_KEY = None
        # File disappeared; clear stale cache so we don't serve old rows.
        _reload()
        return
    if key == _CACHE_KEY:
        return
    _reload()
    _CACHE_KEY = key


def read_recent(
    *,
    limit: int = 50,
    platform: str | None = None,
    action: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(entries, total_in_file)``. Most-recent first.

    ``entries`` are JSON-decoded dicts ready for the API layer to coerce
    into ``ActivityEntry`` pydantic models. ``total_in_file`` is the
    raw row count *before* tail-limit + filtering so the UI can show
    "showing 50 of N". ``platform`` and ``action`` are exact-match
    filters; ``None`` means no filter.
    """
    if limit < 1:
        limit = 1
    _ensure_cache()
    rows = _CACHE_ENTRIES
    if platform is not None:
        rows = [r for r in rows if r.get("platform") == platform]
    if action is not None:
        rows = [r for r in rows if r.get("action") == action]
    # JSONL is append-only chronological; reverse for most-recent-first
    # then slice. ``list.reverse`` is O(n); on 88 rows this is trivial.
    rows = list(reversed(rows))[:limit]
    return rows, _CACHE_TOTAL
