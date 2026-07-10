"""Telegram + queue approval gates for the recipe ideator.

Two gates per approved seed:
    1. Idea gate    — yes/skip/edit on candidate (title + why_now + evidence)
    2. Seed gate    — yes/skip on enriched JSON (full ingredients/method)

Each gate writes a pending item to the appropriate queue file BEFORE sending
the Telegram message, then polls both Telegram AND the queue file (for web_ui
decisions) inside the blocking loop. This is the dual-channel pattern already
used by the comment-composer flow.

Reuses lib/notifier primitives (send + _get_updates + _get_latest_offset)
rather than calling request_approval(), because the existing helper hardcodes
a comment-shaped message format.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

import notifier  # noqa: E402

from .research import Candidate

logger = logging.getLogger(__name__)

Action = Literal["approved", "skipped", "edited", "timeout", "pending"]


@dataclass(frozen=True)
class ApprovalResult:
    action: Action
    edited_text: str | None = None  # populated when user replied "edit: <text>"


# ---------------------------------------------------------------------------
# Queue helpers
# ---------------------------------------------------------------------------


def _append_to_queue(path: Path, item: dict[str, Any]) -> None:
    """Append a new pending item to the queue file (creates if missing)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data: list[Any] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = []
    else:
        data = []
    data.append(item)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _finalize_queue_item(path: Path, item_id: str, action: str) -> None:
    """Stamp the queue item with its final status."""
    from api import state as _state  # noqa: PLC0415

    status: _state.DecisionStatus = "approved" if action == "approved" else "USER_SKIPPED"
    decided_by: _state.DecidedBy = "timeout" if action == "timeout" else "telegram"
    _state.commit_decision(
        path,
        item_id,
        status=status,
        decided_by=decided_by,
        decided_at=datetime.utcnow().isoformat() + "Z",
    )


def _check_queue_for_decision(
    queue_path: Path, item_id: str
) -> ApprovalResult | None:
    """Return an ApprovalResult if the web_ui has stamped the item; else None.

    Inlines the find_item logic to avoid relying on fcntl in hot-loop contexts
    where the lock is not needed (read-only probe).
    """
    if not queue_path.exists():
        return None
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, list):
        return None
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("id") == item_id and item.get("decided_by"):
            status = item.get("status", "")
            if status == "approved":
                return ApprovalResult(action="approved")
            return ApprovalResult(action="skipped")
    return None


# ---------------------------------------------------------------------------
# Core poll loop
# ---------------------------------------------------------------------------


def _poll_for_reply(
    *,
    timeout_hours: int,
    queue_path: Path | None = None,
    item_id: str | None = None,
) -> ApprovalResult:
    """Block until a Telegram reply OR a web_ui queue decision arrives.

    If ``queue_path`` and ``item_id`` are provided the loop also checks the
    queue file on every iteration — whichever channel fires first wins.
    """
    cfg = notifier._load_config()
    token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id:
        return ApprovalResult(action="pending")

    try:
        offset = notifier._get_latest_offset(token)
    except Exception:  # noqa: BLE001
        return ApprovalResult(action="pending")

    deadline = time.time() + (timeout_hours * 3600)
    poll_interval = 5
    while time.time() < deadline:
        updates = notifier._get_updates(token, offset=offset, timeout=poll_interval)
        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            if str(msg.get("chat", {}).get("id", "")) != str(chat_id):
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            lower = text.lower()
            if lower in ("yes", "y", "approve", "ok"):
                return ApprovalResult(action="approved")
            if lower in ("skip", "s", "no", "n", "reject"):
                return ApprovalResult(action="skipped")
            if lower.startswith("edit:"):
                return ApprovalResult(action="edited", edited_text=text[5:].strip())
            # Unknown reply — ignore and keep waiting

        # Check web_ui queue channel
        if queue_path and item_id:
            decision = _check_queue_for_decision(queue_path, item_id)
            if decision is not None:
                return decision

    return ApprovalResult(action="timeout")


# ---------------------------------------------------------------------------
# Public gates
# ---------------------------------------------------------------------------


def approve_idea(candidate: Candidate, *, timeout_hours: int = 6) -> ApprovalResult:
    """Send candidate to Telegram (and queue), wait for yes/skip/edit reply."""
    from lib.config import settings  # noqa: PLC0415

    slug = re.sub(r"[^a-z0-9]+", "-", candidate.title.lower()).strip("-")
    item_id = f"idea-{slug}-{int(time.time())}"

    queue_item: dict[str, Any] = {
        "id": item_id,
        "type": "idea",
        "status": "pending",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "title": candidate.title,
        "category": candidate.category,
        "why_now": candidate.why_now,
        "evidence": candidate.evidence,
        "seasonal_relevance": candidate.seasonal_relevance,
        "search_demand_estimate": candidate.search_demand_estimate,
    }
    _append_to_queue(settings.paths.ideator_queue, queue_item)  # type: ignore[union-attr]
    logger.info("idea queued: %s (id=%s)", candidate.title, item_id)

    msg = (
        f"🍳 <b>Recipe idea — approval needed</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Title:</b> {candidate.title}\n"
        f"<b>Category:</b> <code>{candidate.category}</code>\n"
        f"<b>Demand:</b> {candidate.search_demand_estimate.upper()} · "
        f"seasonal {candidate.seasonal_relevance}/10\n"
        f"<b>Why now:</b> {candidate.why_now}\n"
        f"<b>Evidence:</b> <i>{candidate.evidence[:300]}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Reply: <b>yes</b> · <b>skip</b> · <b>edit: New Title</b>\n"
        f"⏳ {timeout_hours}h to reply"
    )
    if not notifier.send(msg, silent=False):
        return ApprovalResult(action="pending")
    logger.info("idea approval sent: %s", candidate.title)

    result = _poll_for_reply(
        timeout_hours=timeout_hours,
        queue_path=settings.paths.ideator_queue,  # type: ignore[union-attr]
        item_id=item_id,
    )
    _finalize_queue_item(settings.paths.ideator_queue, item_id, result.action)  # type: ignore[union-attr]

    ack = {
        "approved": f"✅ Approved: <i>{candidate.title}</i> — enriching now.",
        "skipped": f"⏭️ Skipped: <i>{candidate.title}</i>.",
        "edited": "✅ Title edited — enriching with your version.",
        "timeout": f"⏰ Timed out: <i>{candidate.title}</i>.",
        "pending": "(no Telegram — saved as pending)",
    }.get(result.action, "")
    if ack:
        notifier.send(ack, silent=True)
    return result


def approve_seed(seed: dict[str, Any], *, timeout_hours: int = 6) -> ApprovalResult:
    """Send the enriched seed JSON for final approval (yes/skip only)."""
    from lib.config import settings  # noqa: PLC0415

    item_id = f"seed-{seed.get('id', '')}"

    queue_item: dict[str, Any] = {
        "id": item_id,
        "type": "seed",
        "status": "pending",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "seed_id": seed.get("id"),
        "title": seed.get("title"),
        "ingredients": seed.get("ingredients", []),
        "prep_minutes": seed.get("prep_minutes"),
        "cook_minutes": seed.get("cook_minutes"),
        "yield_servings": seed.get("yield_servings"),
        "tags": seed.get("tags", []),
        "dog_safety_notes": seed.get("dog_safety_notes", ""),
    }
    _append_to_queue(settings.paths.ideator_queue, queue_item)  # type: ignore[union-attr]
    logger.info("seed queued: %s (id=%s)", seed.get("id"), item_id)

    ingredients = "\n".join(f"• {i}" for i in seed.get("ingredients", []))
    msg = (
        f"📋 <b>Recipe seed — final approval</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Title:</b> {seed.get('title')}\n"
        f"<b>ID:</b> <code>{seed.get('id')}</code>\n"
        f"<b>Prep/Cook:</b> {seed.get('prep_minutes')}min / {seed.get('cook_minutes')}min\n"
        f"<b>Yield:</b> {seed.get('yield_servings')}\n"
        f"<b>Tags:</b> <code>{', '.join(seed.get('tags', []))}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Ingredients:</b>\n<pre>{ingredients[:600]}</pre>\n"
        f"<b>Safety:</b> <i>{seed.get('dog_safety_notes', '')[:300]}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Reply: <b>yes</b> (add to queue) · <b>skip</b> (drop)\n"
        f"⏳ {timeout_hours}h"
    )
    if not notifier.send(msg, silent=False):
        return ApprovalResult(action="pending")
    logger.info("seed approval sent: %s", seed.get("id"))

    result = _poll_for_reply(
        timeout_hours=timeout_hours,
        queue_path=settings.paths.ideator_queue,  # type: ignore[union-attr]
        item_id=item_id,
    )
    _finalize_queue_item(settings.paths.ideator_queue, item_id, result.action)  # type: ignore[union-attr]

    ack = {
        "approved": f"✅ Added to queue: <code>{seed.get('id')}</code>.",
        "skipped": f"⏭️ Dropped: <code>{seed.get('id')}</code>.",
        "edited": "(edit not supported at seed gate — treating as approved)",
        "timeout": f"⏰ Timed out — <code>{seed.get('id')}</code> dropped.",
        "pending": "(no Telegram — seed left pending)",
    }.get(result.action, "")
    if ack:
        notifier.send(ack, silent=True)
    # Treat "edited" at seed gate as approved (we don't accept edits to JSON)
    if result.action == "edited":
        return ApprovalResult(action="approved")
    return result
