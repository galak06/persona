"""
Comment Approver — auto-approves engagement-comment queue items (Phase 3).

The engagement-comment flow is now autonomous: scanners draft the comment
inline (via ``lib/draft_helper.py``) and append items to ``comment_queue.json``.
This script drains the queue's ``pending`` items and stamps each one with
``status=approved, decided_by=auto`` (or ``USER_SKIPPED`` if the draft is empty
because the inline LLM call failed) so ``comment_poster.py`` can pick them up.

No Telegram round-trip happens here anymore for engagement comments — that
gate has been retired. Blog-post pairs (``scripts/content_pipeline.py``) and
group-join requests (``lib/group_discovery/approval.py``) still use Telegram
through their own code paths; this script does NOT touch those flows.

Queue state transitions (this script):
    pending (draft non-empty) → approved        decided_by=auto
    pending (draft empty)     → USER_SKIPPED    decided_by=auto

Usage:
    python scripts/comment_approver.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

UTC = UTC

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.activity_log import log_trace
from lib.bootstrap import init_script
settings, log = init_script(__name__)

from lib.logger import enable_unbuffered, log  # type: ignore[unused-ignore,import-not-found]


from lib.notifier import skill_finished, skill_started  # type: ignore[unused-ignore,import-not-found]
from lib.queue_state import (  # type: ignore[unused-ignore,import-not-found]
    commit_telegram_decision,
    write_pending,
)

from lib.comment_queue_routing import parse_platform_arg, queue_path_for

# Per-platform loop: `--platform instagram|facebook` drains only that queue;
# absent (or `--platform wordpress`) drains the legacy shared queue.
PLATFORM = parse_platform_arg(sys.argv)
QUEUE_FILE = queue_path_for(PLATFORM)
LOG_FILE = PROJECT_ROOT / "logs/engagement_log.jsonl"


def load_json(path: Path, default: Any) -> Any:
    """Read JSON from ``path`` or return ``default`` if the file is missing."""
    if path.exists():
        return json.loads(path.read_text())
    return default


def _log_event(event: dict[str, Any]) -> None:
    """Emit a single-line structured JSON event."""
    log(json.dumps(event, ensure_ascii=False, sort_keys=True))


def run() -> None:
    log("=== Comment Approver ===")
    log_trace("system", "Started Comment Approver (Auto)")

    queue: list[dict[str, Any]] = load_json(QUEUE_FILE, [])
    pending: list[dict[str, Any]] = [
        q for q in queue
        if q.get("status") == "pending"
        and (PLATFORM is None or q.get("platform") == PLATFORM)
    ]

    log(f"Pending (auto-approve): {len(pending)}")

    if not pending:
        log("Nothing to do — no pending items.")
        log_trace("system", "Approver finished: no pending items")
        return


    skill_started("comment-approver", f"Auto-approving {len(pending)} items")

    approved = 0
    skipped = 0
    failed = 0

    for item in pending:
        group = (
            item.get("group_name")
            or item.get("hashtag")
            or item.get("parent_post_title", "")
        )

        # Phase 3: every engagement comment is stamped on disk via write_pending
        # so it picks up a canonical id, then auto-decided in one shot — no
        # Telegram round-trip, no human gate. Empty drafts (inline Gemini call
        # failed in the scanner) are skipped so the poster never tries to send
        # an empty comment.
        item_id = write_pending(QUEUE_FILE, item)
        item["id"] = item_id

        draft = item.get("draft_comment", "") or ""
        platform = item.get("platform", "unknown")

        if not draft.strip():
            status = "USER_SKIPPED"
            text: str | None = None
            _log_event(
                {
                    "event": "auto_skip_empty_draft",
                    "item_id": item_id,
                    "platform": platform,
                    "group_or_hashtag": group,
                }
            )
        else:
            status = "approved"
            text = draft
            _log_event(
                {
                    "event": "auto_approved",
                    "item_id": item_id,
                    "platform": platform,
                    "group_or_hashtag": group,
                    "draft_len": len(draft),
                }
            )

        result = commit_telegram_decision(
            QUEUE_FILE,
            item_id,
            status=status,
            decided_by="auto",
            text=text,
        )

        if result == "committed":
            if status == "approved":
                approved += 1
            else:
                skipped += 1
        elif result == "already_decided":
            # Another channel (web UI) beat us to it — don't double-count.
            _log_event(
                {
                    "event": "auto_skip_already_decided",
                    "item_id": item_id,
                    "platform": platform,
                }
            )
        else:  # "not_found"
            failed += 1
            _log_event(
                {
                    "event": "auto_commit_not_found",
                    "item_id": item_id,
                    "platform": platform,
                }
            )

        # Mirror in-memory so any downstream consumer reading the same list sees
        # the new status. The on-disk truth was already written above.
        item["status"] = status
        item["decided_by"] = "auto"
        item["decided_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        if text is not None:
            item["draft_comment"] = text

    summary = f"Approved: {approved} | Skipped: {skipped} | Failed: {failed}"
    log(f"=== Done === {summary}")
    log_trace("system", f"Approver finished: {approved} approved, {skipped} skipped")
    skill_finished("comment-approver", summary)


if __name__ == "__main__":
    run()
