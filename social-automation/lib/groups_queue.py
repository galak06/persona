# pyright: reportMissingImports=false
"""Read/write helpers for ``.claude/state/pending_groups.json`` (the
producer-side file the ``fb-group-scout`` scanner writes) plus a join-cap
gate that reads ``logs/engagement_log.jsonl`` to enforce the 5/day, 15/week
rate limit.

Why this lives in ``lib/``: the approval API (``api/approval_api.py``)
needs typed access to the pending groups queue and a per-call cap check
before triggering ``send_join_requests``. The producer (``fb-group-scout``)
writes its own discovery payload here without ``id``/``status``/``decided_by``
— this module synthesises those at read time so the API surface is uniform.

All mutations are gated by ``fcntl.flock(LOCK_EX)`` against the queue-file
FD and serialised with atomic ``tmp + os.replace`` writes to defeat the
classic "two writers, last one wins" race.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

# Make ``api/`` importable under the lib-prepended sys.path used by the
# rest of social-automation (scripts insert ``lib/`` first, not the repo
# root). ``api/`` is one level up from this file.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from api.schemas import GroupItem

__all__ = [
    "DAILY_JOIN_CAP",
    "ENGAGEMENT_LOG_PATH",
    "PENDING_GROUPS_PATH",
    "WEEKLY_JOIN_CAP",
    "commit_group_decision",
    "read_pending_groups",
    "under_join_cap",
]

PENDING_GROUPS_PATH: Path = _REPO_ROOT / ".claude" / "state" / "pending_groups.json"
ENGAGEMENT_LOG_PATH: Path = _REPO_ROOT / "logs" / "engagement_log.jsonl"

# Rate caps come from social-automation/CLAUDE.md
# (facebook.group_join_requests_per_{day,week}). Hard-coded here because
# the cap is the contract; config.json is for tunable knobs.
DAILY_JOIN_CAP: int = 5
WEEKLY_JOIN_CAP: int = 15

# Actions that count toward the join cap in engagement_log.jsonl. The
# scout writes ``group_join_request`` today; the spec calls it
# ``group_join`` going forward. Accept both so the cap stays correct
# across the cut-over.
_JOIN_ACTIONS: frozenset[str] = frozenset({"group_join", "group_join_request"})

_log = logging.getLogger(__name__)


def _derive_group_id(url: str) -> str:
    """12-char sha256 of the group URL. Stable across producer + API."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def _normalize_group(raw: dict[str, Any]) -> dict[str, Any]:
    """Stamp ``id``/``status``/``decided_by``/``decided_at``/``type``.

    Mutates a *copy*; the on-disk file is only rewritten by
    ``commit_group_decision``. Pre-existing values win — this is purely a
    read-time backfill for items the scout wrote before Phase 1.
    """
    out = dict(raw)
    url = out.get("url")
    if not isinstance(url, str) or not url:
        # Producer-side bug: skip these rather than crash the whole feed.
        return {}
    out.setdefault("id", _derive_group_id(url))
    out.setdefault("type", "group_to_join")
    status = out.get("status")
    if not isinstance(status, str) or status == "":
        out["status"] = "pending"
    out.setdefault("decided_by", None)
    out.setdefault("decided_at", None)
    out.setdefault("created_at", out.get("added_to_pending"))
    return out


def _read_raw() -> list[dict[str, Any]]:
    """Return the on-disk list. Missing / malformed file → ``[]``."""
    if not PENDING_GROUPS_PATH.exists():
        return []
    try:
        raw = PENDING_GROUPS_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning('{"event": "pending_groups_decode_failed"}')
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def read_pending_groups() -> list[GroupItem]:
    """Return all group items still awaiting a join decision.

    Synthesises ``id``/``status``/``decided_by`` for pre-Phase-1 producer
    rows. Skips rows missing the mandatory ``url`` (producer bug). Items
    with ``decided_by`` set or a terminal status are filtered out so the
    UI only sees actionable rows.
    """
    out: list[GroupItem] = []
    for raw in _read_raw():
        normalised = _normalize_group(raw)
        if not normalised:
            continue
        if normalised.get("decided_by"):
            continue
        if normalised["status"] not in ("pending", ""):
            continue
        try:
            out.append(GroupItem.model_validate(normalised))
        except (ValueError, TypeError) as exc:
            _log.warning(
                '{"event": "pending_group_invalid", "id": "%s", "error": "%s"}',
                normalised.get("id"),
                exc,
            )
    return out


def _atomic_write(data: list[dict[str, Any]]) -> None:
    """Atomic ``tmp + os.replace`` write to ``PENDING_GROUPS_PATH``."""
    tmp = PENDING_GROUPS_PATH.with_suffix(PENDING_GROUPS_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PENDING_GROUPS_PATH)


def commit_group_decision(
    group_id: str,
    *,
    status: Literal["approved", "USER_SKIPPED"],
    decided_by: Literal["telegram", "web_ui", "auto"],
    decided_at: str,
) -> Literal["committed", "already_decided", "not_found"]:
    """Record an approve/skip decision on a pending group. Idempotent.

    Held under ``fcntl.flock(LOCK_EX)`` on the queue file FD so a
    concurrent web-UI + Telegram approval can't double-commit. After the
    lock is acquired we re-read from the path so a stale FD (the inode
    swap subtlety documented in ``api/state.py``) can't clobber another
    writer's just-committed decision.
    """
    if not PENDING_GROUPS_PATH.exists():
        PENDING_GROUPS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PENDING_GROUPS_PATH.write_text("[]", encoding="utf-8")

    with PENDING_GROUPS_PATH.open("r+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            try:
                raw = PENDING_GROUPS_PATH.read_text(encoding="utf-8")
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
                url = candidate.get("url")
                cand_id = candidate.get("id") or (
                    _derive_group_id(url) if isinstance(url, str) and url else ""
                )
                if cand_id == group_id:
                    target_idx = idx
                    break
            if target_idx is None:
                return "not_found"

            target = data[target_idx]
            if target.get("decided_by") or target.get("status") in (
                "approved",
                "USER_SKIPPED",
            ):
                return "already_decided"

            target["id"] = group_id
            target["status"] = status
            target["decided_by"] = decided_by
            target["decided_at"] = decided_at
            data[target_idx] = target
            _atomic_write(data)
            _log.info(
                '{"event": "group_decision_committed", "id": "%s", "status": "%s", "decided_by": "%s"}',
                group_id,
                status,
                decided_by,
            )
            return "committed"
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _count_joins_since(cutoff_iso_date: str) -> int:
    """Count ``group_join`` (or legacy ``group_join_request``) rows in
    the engagement log on/after the given YYYY-MM-DD cutoff."""
    if not ENGAGEMENT_LOG_PATH.exists():
        return 0
    count = 0
    with ENGAGEMENT_LOG_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("action") not in _JOIN_ACTIONS:
                continue
            entry_date = entry.get("date", "")
            if isinstance(entry_date, str) and entry_date >= cutoff_iso_date:
                count += 1
    return count


def under_join_cap() -> tuple[bool, str]:
    """Gate before approving a group-join.

    Returns ``(allowed, reason)``. Caps come from CLAUDE.md:
    5 join-requests/day, 15 join-requests/week. ``reason`` is empty when
    allowed; otherwise it's a short human-readable string the API layer
    surfaces in the 429 body.
    """
    today_iso = date.today().isoformat()
    week_ago_iso = (date.today() - timedelta(days=7)).isoformat()
    today_count = _count_joins_since(today_iso)
    if today_count >= DAILY_JOIN_CAP:
        return False, f"daily cap reached ({today_count}/{DAILY_JOIN_CAP})"
    week_count = _count_joins_since(week_ago_iso)
    if week_count >= WEEKLY_JOIN_CAP:
        return False, f"weekly cap reached ({week_count}/{WEEKLY_JOIN_CAP})"
    return True, ""


def utc_now_iso() -> str:
    """ISO-8601 UTC timestamp helper, matching ``lib/queue_state.utc_now_iso``."""
    return datetime.now(UTC).isoformat(timespec="seconds")
