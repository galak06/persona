#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""migrate_groups_tracker_notes.py — convert legacy string `notes` to dict list.

The Facebook group tracker historically stored `notes` as a single
string with an inline `[ISO] text` prefix. The new contract is a list
of `{at, text}` dicts (one per note). This migration walks
`groups_tracker.json`, finds every row whose `notes` is a string,
parses the `[ISO]` prefix, and rewrites it as `[{"at": <iso>, "text":
<rest>}]`. List rows are left untouched. Rows without a `notes` key
are left untouched.

The script is idempotent: a second invocation finds zero string-shaped
notes and exits with `Nothing to migrate`. It is atomic: the file on
disk is replaced only after a timestamped backup is taken (override
with `--no-backup`). Use `--dry-run` to log what would change without
touching disk.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Make `lib.*` importable when run as `python3 scripts/...`.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lib.io.jsonio import write_json  # noqa: E402

_LEGACY_RE = re.compile(r"^\[(?P<at>[^\]]+)\]\s*(?P<text>.*)$", re.DOTALL)


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _backup_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _parse_legacy(raw: str) -> dict[str, str]:
    """Parse a legacy `[ISO] text` string into {at, text}.

    If the `[ISO]` prefix is missing or malformed, fall back to
    `now` and keep the original raw value as the text — never drop
    data.
    """
    match = _LEGACY_RE.match(raw)
    if match:
        return {"at": match.group("at").strip(), "text": match.group("text").strip()}
    return {"at": _now_iso_utc(), "text": raw}


def _resolve_brand_dir(arg: str | None) -> Path:
    """CLI flag wins, else $BRAND_DIR, else sibling `../persona`."""
    if arg:
        return Path(arg).resolve()
    env = os.environ.get("BRAND_DIR")
    if env:
        return Path(env).resolve()
    return (_REPO_ROOT.parent / "persona").resolve()


def migrate(tracker_path: Path, *, dry_run: bool, backup: bool, logger: logging.Logger) -> dict[str, int]:
    """Run the migration. Returns a counts dict; never raises on schema-shape variance."""
    if not tracker_path.exists():
        raise FileNotFoundError(f"tracker not found: {tracker_path}")

    raw_data: Any = json.loads(tracker_path.read_text(encoding="utf-8"))
    if not isinstance(raw_data, list):
        raise ValueError(f"expected top-level list in {tracker_path}, got {type(raw_data).__name__}")

    rows: list[Any] = raw_data
    string_rows = [g for g in rows if isinstance(g, dict) and isinstance(g.get("notes"), str)]
    mixed_rows = [
        g for g in rows
        if isinstance(g, dict)
        and isinstance(g.get("notes"), list)
        and any(not isinstance(n, dict) for n in g["notes"])
    ]

    counts = {
        "rows_total": len(rows),
        "rows_transformed": 0,
        "notes_transformed": 0,
        "embedded_notes_transformed": 0,
        "strings_remaining_after": 0,
        "embedded_strings_remaining_after": 0,
    }

    if not string_rows and not mixed_rows:
        logger.info(
            "Nothing to migrate — 0 string-shaped notes and 0 embedded string items in %d rows",
            len(rows),
        )
        return counts

    if string_rows:
        logger.info("Found %d rows with top-level string notes (of %d total)", len(string_rows), len(rows))

    if mixed_rows:
        logger.info("Found %d rows with embedded string items in list notes (of %d total)", len(mixed_rows), len(rows))

    # Pass 1 — convert top-level string notes to single-element dict lists.
    for row in string_rows:
        legacy = row["notes"]
        parsed = _parse_legacy(legacy)
        row["notes"] = [parsed]
        counts["rows_transformed"] += 1
        counts["notes_transformed"] += 1
        gid = row.get("id") or row.get("group_id") or row.get("name") or "<unknown>"
        logger.info("Converted row id=%s at=%s text=%r", gid, parsed["at"], parsed["text"][:60])

    # Pass 2 — fix embedded string items inside list-shaped notes.
    for row in mixed_rows:
        new_notes: list[dict[str, str]] = []
        converted_in_row = 0
        for n in row["notes"]:
            if isinstance(n, dict):
                new_notes.append(n)
            else:
                parsed = _parse_legacy(str(n))
                new_notes.append(parsed)
                converted_in_row += 1
                gid = row.get("id") or row.get("group_id") or row.get("name") or "<unknown>"
                logger.info(
                    "Converted embedded item row id=%s at=%s text=%r",
                    gid,
                    parsed["at"],
                    parsed["text"][:60],
                )
        row["notes"] = new_notes
        counts["rows_transformed"] += 1
        counts["embedded_notes_transformed"] += converted_in_row
        counts["notes_transformed"] += converted_in_row

    counts["strings_remaining_after"] = sum(
        1 for g in rows if isinstance(g, dict) and isinstance(g.get("notes"), str)
    )
    counts["embedded_strings_remaining_after"] = sum(
        1
        for g in rows
        if isinstance(g, dict) and isinstance(g.get("notes"), list)
        for n in g["notes"]
        if not isinstance(n, dict)
    )

    if dry_run:
        logger.info("DRY RUN — no write. Would transform %d rows.", counts["rows_transformed"])
        return counts

    if backup:
        backup_path = tracker_path.parent / f"groups_tracker.backup-{_backup_stamp()}.json"
        shutil.copy2(tracker_path, backup_path)
        logger.info("Backup written to %s", backup_path)

    write_json(tracker_path, rows, atomic=True, indent=2)
    logger.info("Wrote %s atomically (%d rows, %d notes converted)", tracker_path, counts["rows_total"], counts["notes_transformed"])
    return counts


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--brand-dir", default=None, help="Brand dir (default: $BRAND_DIR or ../persona)")
    parser.add_argument("--dry-run", action="store_true", help="Log changes without writing to disk")
    parser.add_argument("--no-backup", action="store_true", help="Skip the timestamped backup before write")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger = logging.getLogger("migrate_groups_tracker_notes")

    args = _parse_args(argv)
    brand_dir = _resolve_brand_dir(args.brand_dir)
    tracker = brand_dir / "data" / "groups_tracker.json"
    logger.info("Tracker: %s | dry_run=%s | backup=%s", tracker, args.dry_run, not args.no_backup)

    counts = migrate(tracker, dry_run=args.dry_run, backup=not args.no_backup, logger=logger)
    logger.info("Counts: %s", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
