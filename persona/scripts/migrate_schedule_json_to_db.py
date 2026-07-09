#!/usr/bin/env python3
"""Migrate schedule.json → schedule.db (idempotent, additive-only)."""
import json
import os
import sys
from pathlib import Path

# add social-automation to sys.path so lib.schedule_db is importable
# script lives at social-automation/scripts/, so parent is social-automation/
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib import schedule_db


def main() -> None:
    brand_dir = os.environ.get("BRAND_DIR")
    if not brand_dir:
        print("ERROR: BRAND_DIR env var required", file=sys.stderr)
        sys.exit(1)

    brand_path = Path(brand_dir)
    json_path = brand_path / "schedule.json"
    db_path = brand_path / "data" / "db" / "schedule.db"

    if not json_path.exists():
        print(f"ERROR: {json_path} not found", file=sys.stderr)
        sys.exit(1)

    data = json.loads(json_path.read_text())
    tasks = data.get("tasks", [])

    conn = schedule_db.connect(str(db_path))
    count = 0
    for task in tasks:
        schedule_db.save_task(conn, task)
        count += 1
    conn.commit()
    conn.close()

    print(f"Migrated {count} tasks from {json_path} → {db_path}")


if __name__ == "__main__":
    main()
