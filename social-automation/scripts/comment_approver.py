"""
Comment Approver — sends pending queue items to Telegram for approval and
updates their status based on the user's reply.

Runs independently from comment_poster.py so the long Telegram wait (up to 12h
per item) doesn't trip the watchdog's stuck-process detector. This script
should NOT be wrapped in run_with_watchdog.py.

Queue state transitions:
    pending → approved       (user replied yes / edit:)
    pending → USER_SKIPPED   (user replied skip / timeout / unknown)
    pending → pending        (Telegram unreachable — retry next run)

Usage:
    python scripts/comment_approver.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC
from pathlib import Path

UTC = UTC

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from logger import enable_unbuffered, log_step

enable_unbuffered()

from notifier import request_approval, skill_finished, skill_started

QUEUE_FILE = PROJECT_ROOT / ".claude/state/comment_queue.json"
LOG_FILE = PROJECT_ROOT / "logs/engagement_log.jsonl"


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def build_engagement_history() -> set[str]:
    """Load set of groups/hashtags we've previously posted to."""
    groups: set[str] = set()
    if LOG_FILE.exists():
        for line in LOG_FILE.open():
            try:
                entry = json.loads(line)
                if entry.get("action") in ("comment", "like"):
                    groups.add(entry["target_name"])
            except Exception:
                continue
    return groups


def run() -> None:
    print("=== Comment Approver ===\n", flush=True)

    queue = load_json(QUEUE_FILE, [])
    pending = [q for q in queue if q.get("status") == "pending" and q.get("draft_comment")]

    print(f"Pending (need approval): {len(pending)}", flush=True)

    if not pending:
        print("Nothing to do — no pending items.", flush=True)
        return

    skill_started("comment-approver", f"Requesting approval for {len(pending)} items")

    previously_posted = build_engagement_history()

    approved = 0
    skipped = 0
    still_pending = 0

    for item in pending:
        group = item.get("group_name") or item.get("hashtag") or item.get("parent_post_title", "")
        is_new = group not in previously_posted
        needs_approval = (
            item.get("requires_approval", False)
            or item["platform"] == "instagram"
            # WP replies land on our own site under the Nalla's Dad byline —
            # every one is a public-facing post from the brand, so route
            # through Telegram until we have a track record of auto-approval
            # not producing cringey replies.
            or item["platform"] == "wordpress"
            or "dogfoodandfun.com" in item.get("draft_comment", "").lower()
            or is_new
        )

        if not needs_approval:
            item["status"] = "approved"
            approved += 1
            save_json(QUEUE_FILE, queue)
            continue

        log_step(f"Requesting approval: {group}")
        result = request_approval(
            platform=item["platform"],
            group_or_hashtag=group,
            post_preview=item["post_text"][:200],
            draft_comment=item["draft_comment"],
            relevance_score=item["relevance_score"],
            timeout_hours=12,
        )

        action = result["action"]
        if action in ("approved", "edited"):
            item["status"] = "approved"
            item["draft_comment"] = result["comment"]
            approved += 1
        elif action == "pending":
            print("  Telegram unreachable — leaving pending for next run", flush=True)
            still_pending += 1
        else:
            item["status"] = "USER_SKIPPED"
            skipped += 1

        save_json(QUEUE_FILE, queue)

    summary = f"Approved: {approved} | Skipped: {skipped} | Still pending: {still_pending}"
    print(f"\n=== Done === {summary}", flush=True)
    skill_finished("comment-approver", summary)


if __name__ == "__main__":
    run()
