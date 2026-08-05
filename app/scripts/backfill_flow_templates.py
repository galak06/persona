#!/usr/bin/env python3
"""Seed `flow_templates` (Postgres) from `profiles/*.json`.

`profiles/*.json` stays the human-edited source file; this script is how an
edit there reaches `lib/brand_provisioning.py`'s actual read path (Postgres)
-- mirrors the schedule_tasks upsert pattern in
`scripts/backfill_brand_registration.py`. Idempotent: safe to re-run after
any profile edit.

Usage::

    python scripts/backfill_flow_templates.py --dry-run
    python scripts/backfill_flow_templates.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from lib import flow_templates_db

_PROFILES_DIR = _ENGINE_ROOT / "profiles"
_PROFILE_FILES = ("_engine.json", "facebook.json", "instagram.json", "wordpress.json")


def _flow_to_row(flow: dict[str, Any], *, platform: str) -> dict[str, Any]:
    flow_id = str(flow["id"])
    return {
        "id": flow_id,
        "platform": platform,
        "title": flow_id,
        "description": flow.get("description", ""),
        "order_num": flow.get("order", 0),
        "script": flow.get("script"),
        "skill": flow.get("skill"),
        "args": flow.get("args", []),
        "depends_on": flow.get("depends_on", []),
        "requires_approval": bool(flow.get("requires_approval", False)),
        "approval_channel": flow.get("approval_channel"),
        "requires_browser": bool(flow.get("requires_browser", False)),
        "re_run_guard": bool(flow.get("re_run_guard", True)),
        "output_file": flow.get("output_file"),
        "schedule": flow.get("schedule", {}),
        "inputs": flow.get("inputs", []),
        "telegram_notify": bool(flow.get("telegram_notify", True)),
    }


def load_rows() -> list[dict[str, Any]]:
    """Every `flows[]` entry across `profiles/*.json`, as flow_templates rows."""
    rows: list[dict[str, Any]] = []
    for filename in _PROFILE_FILES:
        path = _PROFILES_DIR / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        platform = str(data.get("platform") or filename.removesuffix(".json"))
        for flow in data.get("flows", []):
            rows.append(_flow_to_row(flow, platform=platform))
    return rows


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="print what would be written")
    group.add_argument("--apply", action="store_true", help="write the rows")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    rows = load_rows()

    print(f"{len(rows)} flow templates found across {len(_PROFILE_FILES)} profile files:")
    for row in rows:
        print(f"  {row['platform']:10s} {row['id']}")

    if not args.apply:
        print("\n(dry run -- nothing written; pass --apply to write)")
        return 0

    for row in rows:
        flow_templates_db.save(row)
    print(f"\nupserted {len(rows)} flow_templates rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
