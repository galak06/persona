#!/usr/bin/env python3
"""Copy `completed_tasks` dedup rows from the legacy `'persona'` scope to a real brand.

Until 2026-08-15 `lib/dedup_pg.py` hardcoded ``_BRAND = "persona"`` as the
default for all four of its functions, and no caller ever passed ``brand=``.
Every like/comment/scan dedup row was therefore written under a brand id that
does not exist in the `brands` table, while every other module in the codebase
derived the real slug. `dedup_pg` now resolves the brand through
`lib.brand_context`, which makes those legacy rows invisible to it.

**Run this before the `dedup_pg` change reaches production.** Without it, every
previously-engaged post reads as fresh on the next scan and the engagers will
re-like and re-comment on posts they have already touched.

Additive by construction: rows are COPIED, never updated or deleted, so the
legacy `'persona'` scope stays exactly as it was and the script is safe to run
twice. The `completed_tasks` primary key is
``(task_type, platform, entity_id, brand)``, so `ON CONFLICT DO NOTHING` makes
re-runs a no-op rather than an error.

Usage::

    python scripts/backfill_dedup_brand.py --dry-run
    python scripts/backfill_dedup_brand.py
    python scripts/backfill_dedup_brand.py --brand acme-dogs --from-brand persona
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.brand_context import current_brand_id
from lib.db import execute, fetch_one

LEGACY_BRAND = "persona"

_COUNT_SQL = "SELECT COUNT(*) AS n FROM completed_tasks WHERE brand = %s"

_COPY_SQL = """
INSERT INTO completed_tasks
    (task_type, platform, entity_id, brand, worker_label, meta, completed_at)
SELECT task_type, platform, entity_id, %(target)s, worker_label, meta, completed_at
FROM completed_tasks
WHERE brand = %(source)s
ON CONFLICT (task_type, platform, entity_id, brand) DO NOTHING
"""


def count_for(brand: str) -> int:
    row = fetch_one(_COUNT_SQL, (brand,))
    return int(row["n"]) if row else 0


def backfill(target: str, source: str = LEGACY_BRAND, *, dry_run: bool = False) -> int:
    """Copy dedup rows from `source` scope into `target`. Returns rows inserted."""
    if target == source:
        raise ValueError(f"target and source brand are both '{target}' -- nothing to copy")

    source_rows = count_for(source)
    before = count_for(target)
    print(f"source '{source}': {source_rows} rows")
    print(f"target '{target}': {before} rows (before)")

    if dry_run:
        print(f"DRY RUN -- would copy up to {source_rows} rows into '{target}'")
        return 0

    execute(_COPY_SQL, {"target": target, "source": source})
    after = count_for(target)
    inserted = after - before
    print(f"target '{target}': {after} rows (after) -- {inserted} inserted")
    print(f"source '{source}': {count_for(source)} rows (unchanged -- copy is additive)")
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--brand",
        default=None,
        help="target brand id (default: resolved from PERSONA_BRAND / BRAND_DIR)",
    )
    parser.add_argument(
        "--from-brand",
        default=LEGACY_BRAND,
        help=f"source scope to copy from (default: {LEGACY_BRAND!r})",
    )
    parser.add_argument("--dry-run", action="store_true", help="report counts, write nothing")
    args = parser.parse_args()

    target = args.brand or current_brand_id()
    if target == "default":
        print(
            "refusing to backfill into the 'default' brand -- set PERSONA_BRAND "
            "or BRAND_DIR, or pass --brand explicitly",
            file=sys.stderr,
        )
        return 2

    try:
        backfill(target, args.from_brand, dry_run=args.dry_run)
    except Exception as exc:
        print(f"backfill failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
