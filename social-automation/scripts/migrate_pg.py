"""One-time Postgres schema migration.

Creates the completed_tasks table used by lib/dedup_pg.py.
Safe to re-run — uses CREATE TABLE IF NOT EXISTS.

Run::
    BRAND_DIR=.../dogfoodandfun python scripts/migrate_pg.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.bootstrap import init_script
from lib.pg_db import db_cursor, health_check

_, log = init_script(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS completed_tasks (
    task_type    VARCHAR(50)   NOT NULL,
    platform     VARCHAR(20)   NOT NULL,
    entity_id    VARCHAR(255)  NOT NULL,
    brand        VARCHAR(100)  NOT NULL,
    worker_label VARCHAR(100)  NOT NULL DEFAULT '',
    meta         JSONB         NOT NULL DEFAULT '{}',
    completed_at TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (task_type, platform, entity_id, brand)
);

CREATE INDEX IF NOT EXISTS idx_completed_tasks_brand
    ON completed_tasks (brand, task_type, platform);

CREATE INDEX IF NOT EXISTS idx_completed_tasks_at
    ON completed_tasks (completed_at DESC);
"""

if __name__ == "__main__":
    if not health_check():
        print("ERROR: cannot reach Postgres — check PG_DSN / server status")
        sys.exit(1)

    with db_cursor() as cur:
        cur.execute(SCHEMA)

    print("Migration complete — completed_tasks table ready.")
