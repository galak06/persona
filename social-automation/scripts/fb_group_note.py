"""Update a group's posting mode + add a timestamped note in groups_tracker.json.

The `posting_mode` field tells `fb-group-publisher` how to treat each group:

  - direct          — posts go live immediately
  - admin_approval  — every post hits a mod queue first
  - admins_only     — members can't post (skip entirely)
  - links_blocked   — text posts OK, URL posts removed/rejected
  - blocked         — our account's posts get removed on sight (skip)
  - unknown         — default; treat as admin_approval to be safe

Notes accumulate — each call appends a `{timestamp, text}` entry, nothing is
overwritten except `posting_mode` itself.

Usage:
    # Flag a group as admin-moderated with a note
    python scripts/fb_group_note.py \\
        --id 398460282269029 \\
        --mode admin_approval \\
        --note "Posts go to admin queue. Submitted 2026-04-20."

    # Just add a note without changing mode
    python scripts/fb_group_note.py --id 398460282269029 --note "Post rejected by mod."

    # List all groups with current mode + last note
    python scripts/fb_group_note.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from lib.groups.notes import append_group_note

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRACKER_FILE = settings.paths.groups_tracker

_VALID_MODES = {
    "direct",
    "admin_approval",
    "admins_only",
    "links_blocked",
    "blocked",
    "unknown",
}


def _load() -> list[dict]:
    return json.loads(TRACKER_FILE.read_text()) if TRACKER_FILE.exists() else []


def _save(data: list[dict]) -> None:
    TRACKER_FILE.write_text(json.dumps(data, indent=2))


def _ensure_fields(entry: dict) -> None:
    """Backfill new fields on existing tracker entries."""
    entry.setdefault("posting_mode", "unknown")
    entry.setdefault("notes", [])
    entry.setdefault("last_post_status", None)


def _match(tracker: list[dict], gid: str) -> dict | None:
    for entry in tracker:
        if gid in entry.get("group_url", ""):
            return entry
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Update group tracker notes + posting mode")
    parser.add_argument("--id", help="group id (digits from the URL)")
    parser.add_argument("--mode", choices=sorted(_VALID_MODES), help="set posting mode")
    parser.add_argument("--note", help="append a timestamped note")
    parser.add_argument(
        "--status", help="set last_post_status (e.g. pending_approval / posted / rejected)"
    )
    parser.add_argument(
        "--caption", help="store the caption text posted to this group (used by pending-check)"
    )
    parser.add_argument("--list", action="store_true", help="print current state of every group")
    args = parser.parse_args()

    tracker = _load()
    for entry in tracker:
        _ensure_fields(entry)

    if args.list:
        for entry in tracker:
            last_note = (entry["notes"][-1]["text"] if entry["notes"] else "")[:60]
            members = entry.get("member_count") or "?"
            print(
                f"  {entry['posting_mode']:<15} {members:>7}  "
                f"{entry['group_name'][:45]:<45}  {last_note}"
            )
        _save(tracker)  # persist the field backfill
        return

    if not args.id:
        print("--id <group_id> required (or --list)", file=sys.stderr)
        sys.exit(2)
    if not (args.mode or args.note or args.status or args.caption):
        print("need at least one of --mode / --note / --status / --caption", file=sys.stderr)
        sys.exit(2)

    entry = _match(tracker, args.id)
    if entry is None:
        print(f"no group matches --id={args.id!r}", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if args.mode:
        entry["posting_mode"] = args.mode
    if args.status:
        entry["last_post_status"] = args.status
        entry["last_post_at"] = now
    if args.caption:
        entry["last_post_caption"] = args.caption
    if args.note:
        append_group_note(entry, args.note)

    _save(tracker)
    print(f"  {entry['group_name']}")
    print(f"  → mode={entry['posting_mode']}, status={entry['last_post_status']}")
    if args.note:
        print(f"  + note: {args.note}")


if __name__ == "__main__":
    main()
