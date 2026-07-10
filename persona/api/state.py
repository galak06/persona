"""Atomic, flock-protected JSON queue helpers for the approval API.

Why flock: both the Telegram approver (``scripts/comment_approver.py``) and
this HTTP sidecar will mutate the same queue files. Without an OS-level lock,
two near-simultaneous decisions could clobber each other's writes. We hold
``fcntl.flock(LOCK_EX)`` across the full read-modify-write window, then swap
in the new file with ``os.replace`` for atomic visibility.

Idempotency: ``commit_decision`` returns ``"already_decided"`` when the item
has been resolved by another channel — the API layer maps this to 409. This
is the safety net that lets the web UI and Telegram both fire without fear
of double-posting.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

DecisionStatus = Literal["approved", "USER_SKIPPED", "edited"]
DecidedBy = Literal["telegram", "web_ui", "auto"]
CommitResult = Literal["committed", "already_decided", "not_found"]

# Status values that mean "still awaiting a decision". Items written by the
# pre-Phase-3 producers may only have ``status="pending"`` (or none at all);
# we treat absent ``decided_by`` as not-yet-decided regardless.
_PENDING_STATUSES: frozenset[str] = frozenset({"pending", ""})

# Status values that mean "already resolved through some channel". Used to
# short-circuit a second commit.
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "approved",
        "USER_SKIPPED",
        "edited",
        "posted",
        "duplicate_blocked",
        "already_engaged",
        "COMMENT_BOX_NOT_FOUND",
        "COMMENTS_DISABLED",
    }
)


def derive_item_id(item: dict[str, Any]) -> str:
    """Stable per-item id.

    Prefers an existing ``hash`` / ``id`` field (forward-compat with Phase 3
    producers that pre-stamp one). Falls back to a 12-char sha256 over the
    item's stable identity fields, then to a hash of the whole JSON blob.
    """
    for key in ("id", "hash"):
        existing = item.get(key)
        if isinstance(existing, str) and existing:
            return existing

    platform = str(item.get("platform", ""))
    post_id = str(item.get("post_id", ""))
    if platform and post_id:
        seed = f"{platform}:{post_id}"
    else:
        seed = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    """Stamp ``id`` and a default ``status`` so downstream code is uniform.

    Mutates a *copy*; never the original. Pre-Phase-3 items lack ``id`` and
    sometimes ``status`` — we synthesise both so the web UI sees a clean shape.
    """
    out = dict(item)
    out.setdefault("id", derive_item_id(item))
    status = out.get("status")
    if not isinstance(status, str) or status == "":
        out["status"] = "pending"
    out.setdefault("decided_by", None)
    out.setdefault("decided_at", None)
    return out


def read_queue(path: Path) -> list[dict[str, Any]]:
    """Return all queue items, normalized. Missing / malformed file → ``[]``."""
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [_normalize_item(item) for item in data if isinstance(item, dict)]


def find_item(path: Path, item_id: str) -> dict[str, Any] | None:
    """Locate one item by id. Returns ``None`` when missing."""
    for item in read_queue(path):
        if item.get("id") == item_id:
            return item
    return None


def _is_already_decided(item: dict[str, Any]) -> bool:
    """An item is decided once *either* a terminal status is set *or* a
    ``decided_by`` channel has stamped it."""
    if item.get("decided_by"):
        return True
    status = item.get("status")
    return bool(isinstance(status, str) and status in _TERMINAL_STATUSES)


def _apply_decision(
    item: dict[str, Any],
    *,
    status: DecisionStatus,
    decided_by: DecidedBy,
    decided_at: str,
    channel: str | None,
    text: str | None,
    fb_caption: str | None,
    ig_caption: str | None,
) -> None:
    """Mutate ``item`` in place with the resolved fields."""
    item["status"] = status
    item["decided_by"] = decided_by
    item["decided_at"] = decided_at
    if channel is not None:
        item["channel"] = channel
    if text is not None:
        # Used by comment items — the final text we'll post.
        item["comment_text"] = text
        item["draft_comment"] = text
    if fb_caption is not None:
        item["fb_caption"] = fb_caption
    if ig_caption is not None:
        item["ig_caption"] = ig_caption


def commit_decision(
    path: Path,
    item_id: str,
    *,
    status: DecisionStatus,
    decided_by: DecidedBy,
    decided_at: str,
    channel: str | None = None,
    text: str | None = None,
    fb_caption: str | None = None,
    ig_caption: str | None = None,
) -> CommitResult:
    """Atomically record a decision. Idempotent across channels.

    Returns ``"already_decided"`` when the item exists but another channel
    already committed — the caller should surface this as 409 Conflict. The
    full read-modify-write happens under ``flock(LOCK_EX)`` against an
    on-disk sentinel FD so the second writer blocks until the first finishes.
    """
    if not path.exists():
        # Lock target needs *some* file. Create it as an empty list so other
        # producers can write to it later without surprise.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[]", encoding="utf-8")

    # Open r+ so we can flock the actual queue file FD; this serialises both
    # reads and writes against any other flock-aware process.
    #
    # Subtlety: ``os.replace`` swaps the inode out from under any other
    # process's already-open FD. If writer B opened the file before writer A
    # called ``os.replace``, B's FD still points at the (now unlinked) old
    # inode. After A unlocks and B's flock succeeds, ``fh.read()`` on B's
    # stale FD would return the pre-A content — and B would happily commit
    # again, clobbering A's decision. To dodge this we re-read from the
    # *path* (which always resolves the current inode) once the lock is held.
    with path.open("r+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                raw = ""
            try:
                data = json.loads(raw) if raw.strip() else []
            except json.JSONDecodeError:
                data = []
            if not isinstance(data, list):
                data = []

            target_idx: int | None = None
            for idx, candidate in enumerate(data):
                if not isinstance(candidate, dict):
                    continue
                if derive_item_id(candidate) == item_id or candidate.get("id") == item_id:
                    target_idx = idx
                    break

            if target_idx is None:
                return "not_found"

            target = data[target_idx]
            if _is_already_decided(target):
                return "already_decided"

            _apply_decision(
                target,
                status=status,
                decided_by=decided_by,
                decided_at=decided_at,
                channel=channel,
                text=text,
                fb_caption=fb_caption,
                ig_caption=ig_caption,
            )
            # Stamp id so future reads can match without re-deriving.
            target.setdefault("id", item_id)
            data[target_idx] = target

            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp_path, path)
            return "committed"
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
