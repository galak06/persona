"""One-off grandfather backfill for the fb-engager first-comment gate.

The gate (``fb_groups.first_comment_flagged_at`` / ``first_comment_approved_at``)
skips a group's first-ever inline comment until the user approves it once via
the Groups UI. Groups the brand has ALREADY commented in should behave exactly
as before, so this script pre-approves every group with a previously posted FB
comment, unioned from two sources:

  1. engagements DB — ``platform='facebook', kind='comment', status='posted'``
     rows (``target_name`` is the group name).
  2. ``$BRAND_DIR/logs/engagement_log.jsonl`` — lines with
     ``platform == 'facebook'`` and ``action in ('comment', 'commented')``
     (``target_name`` is the group name; older rows may use ``target``).

Idempotent: already-approved groups are skipped. Names that resolve to no
fb_groups row are reported — those groups just flag once via Telegram on
their first scan and need one click.

Usage::

    python scripts/backfill_first_comment_approvals.py --dry-run
    python scripts/backfill_first_comment_approvals.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from lib import engagements_db, groups_db
from lib.config import settings

_FB_COMMENT_ACTIONS: frozenset[str] = frozenset({"comment", "commented"})


def _names_from_engagements_db() -> set[str]:
    """Group names with a posted FB comment recorded in engagements DB."""
    rows = engagements_db.list_engagements(
        platform="facebook", kind="comment", status="posted", limit=1000
    )
    return {name for r in rows if (name := str(r.get("target_name") or "").strip())}


def _names_from_jsonl(log_path: Path) -> set[str]:
    """Group names with an FB comment line in engagement_log.jsonl."""
    names: set[str] = set()
    if not log_path.exists():
        return names
    with log_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry: Any = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict) or entry.get("platform") != "facebook":
                continue
            if entry.get("action") not in _FB_COMMENT_ACTIONS:
                continue
            target = str(entry.get("target_name") or entry.get("target") or "").strip()
            if target:
                names.add(target)
    return names


def _resolve(name: str) -> dict[str, Any] | None:
    """fb_groups row for a logged target — by URL when it looks like one."""
    if name.startswith(("http://", "https://")):
        return groups_db.get_by_url(name)
    return groups_db.get_by_name(name)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-approve the first-comment gate for groups already commented in."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what WOULD be approved without writing",
    )
    args = parser.parse_args()

    paths = settings.paths
    if paths is None:
        raise SystemExit("settings.paths is not configured (is BRAND_DIR set?)")
    log_path = paths.logs_dir / "engagement_log.jsonl"

    names = sorted(_names_from_engagements_db() | _names_from_jsonl(log_path))
    now_iso = datetime.now(UTC).isoformat()
    approved = already = unresolved = 0
    for name in names:
        group = _resolve(name)
        if group is None:
            print(f"  UNRESOLVED       {name}")
            unresolved += 1
            continue
        display = str(group.get("group_name") or name)
        if str(group.get("first_comment_approved_at") or "").strip():
            print(f"  already approved {display}")
            already += 1
            continue
        if args.dry_run:
            print(f"  WOULD APPROVE    {display}")
        else:
            groups_db.set_first_comment_approved(str(group["group_url"]), now_iso)
            print(f"  APPROVED         {display}")
        approved += 1

    verb = "would approve" if args.dry_run else "approved"
    print(
        f"\n{len(names)} commented-in group name(s): {verb} {approved}, "
        f"{already} already approved, {unresolved} unresolved"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
