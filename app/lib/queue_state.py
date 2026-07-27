# pyright: reportMissingImports=false
"""Producer-facing wrapper around ``api/state.py``.

Why this layer exists:
- ``lib/`` is what the producers (comment_approver, notifier, pipelines) depend
  on. They should not reach into the API package directly — that gives us a
  seam to add caching or a different backend (Redis, SQLite) later without
  touching every producer.
- ``api/state.py`` already owns the flock + atomic-write contract. We DO NOT
  duplicate that logic here — every mutation funnels back through it.

Three concerns this wrapper adds on top of ``api/state.py``:
1. ``write_pending`` — append a new pending item, stamping ``id``, ``status``,
   ``decided_by``, ``decided_at``, ``created_at``. Idempotent on ``id``.
2. ``read_decision`` — narrow lookup used by the notifier poll loop so it
   can short-circuit when the web UI has already decided.
3. ``commit_telegram_decision`` — typed convenience around
   ``api.state.commit_decision`` that pre-fills the UTC timestamp and
   accepts ``decided_by`` from any approval channel (``telegram``,
   ``web_ui``, or ``auto`` for the autonomous engagement-comment flow).
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

# Make ``api/`` importable when this module is loaded via the lib-prepended
# sys.path used by the rest of social-automation (scripts insert ``lib/`` first,
# not the project root). ``api/`` is one level up from this file.
_API_PARENT = Path(__file__).resolve().parent.parent
if str(_API_PARENT) not in sys.path:
    sys.path.insert(0, str(_API_PARENT))

from api.state import (
    CommitResult,
    DecidedBy,
    DecisionStatus,
    commit_decision,
    derive_item_id,
    find_item,
)

__all__ = [
    "commit_telegram_decision",
    "read_decision",
    "utc_now_iso",
    "write_pending",
]


def utc_now_iso() -> str:
    """UTC ISO-8601 timestamp with ``+00:00`` suffix.

    Centralised so producers and tests agree on the format.
    """
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_decision(queue_path: Path, item_id: str) -> dict[str, Any] | None:
    """Return the queue item for ``item_id`` or ``None`` if missing.

    Thin wrapper around ``api.state.find_item`` so the notifier doesn't import
    from ``api`` directly. Returns the *normalised* item shape (id, status,
    decided_by, decided_at always present).
    """
    return find_item(queue_path, item_id)


def write_pending(queue_path: Path, item: dict[str, Any]) -> str:
    """Append a new pending item, returning its assigned ``id``.

    Stamps the Phase-3 fields:
        ``id``         — from ``api.state.derive_item_id`` (or pre-existing ``id``)
        ``status``     — ``"pending"``
        ``decided_by`` — ``None``
        ``decided_at`` — ``None``
        ``created_at`` — UTC ISO-8601 now (only set if absent)

    Idempotent: if an item with the same id already exists in the queue, the
    function returns that id without re-appending. This lets producers call
    ``write_pending`` on every run without worrying about duplicates from
    crash-then-replay scenarios.

    The full read-modify-write happens under ``fcntl.flock(LOCK_EX)`` against
    the queue file FD so concurrent producers can't clobber each other.
    """
    item_id = derive_item_id(item)

    # Ensure file exists so we have something to flock.
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    if not queue_path.exists():
        queue_path.write_text("[]", encoding="utf-8")

    with queue_path.open("r+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            raw = fh.read()
            try:
                data = json.loads(raw) if raw.strip() else []
            except json.JSONDecodeError:
                data = []
            if not isinstance(data, list):
                data = []

            # Idempotency check — if the id already exists, no-op.
            for candidate in data:
                if not isinstance(candidate, dict):
                    continue
                if candidate.get("id") == item_id or derive_item_id(candidate) == item_id:
                    return item_id

            stamped = dict(item)
            stamped["id"] = item_id
            stamped.setdefault("status", "pending")
            stamped.setdefault("decided_by", None)
            stamped.setdefault("decided_at", None)
            stamped.setdefault("created_at", utc_now_iso())
            data.append(stamped)

            tmp_path = queue_path.with_suffix(queue_path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp_path, queue_path)
            return item_id
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def commit_telegram_decision(
    queue_path: Path,
    item_id: str,
    *,
    status: str,
    decided_by: Literal["telegram", "web_ui", "auto"] = "telegram",
    text: str | None = None,
    fb_caption: str | None = None,
    ig_caption: str | None = None,
    channel: str | None = None,
) -> CommitResult:
    """Record an approval-channel decision on a queue item.

    Wraps ``api.state.commit_decision`` with a fresh UTC timestamp. The
    ``decided_by`` parameter defaults to ``"telegram"`` for back-compat
    with the original Telegram-only callers; pass ``"auto"`` from the
    autonomous engagement-comment flow (Phase 3) or ``"web_ui"`` from the
    HTTP sidecar. ``status`` must be one of ``"approved"``,
    ``"USER_SKIPPED"``, or ``"edited"`` — invalid values raise
    ``ValueError`` so producers fail loudly rather than writing garbage
    to the queue.

    Returns the same tri-state as ``api.state.commit_decision``:
        ``"committed"``       — first writer wins, decision persisted.
        ``"already_decided"`` — another channel got there first; caller may
                                 surface this as 409 / "ignore".
        ``"not_found"``       — no item with that id in the queue.
    """
    if status not in ("approved", "USER_SKIPPED", "edited"):
        raise ValueError(f"invalid status: {status!r}")

    typed_status: DecisionStatus = status  # type: ignore[assignment]
    typed_decided_by: DecidedBy = decided_by
    return commit_decision(
        queue_path,
        item_id,
        status=typed_status,
        decided_by=typed_decided_by,
        decided_at=utc_now_iso(),
        channel=channel,
        text=text,
        fb_caption=fb_caption,
        ig_caption=ig_caption,
    )
