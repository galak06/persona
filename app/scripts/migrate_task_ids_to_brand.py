"""Migrate `schedule_tasks.id` (and matching `worker_runs.worker_label`) from
the legacy `dogfood-` prefix to the brand-derived `<brand_id>-` form.

The two conventions disagreed: `lib.brand_provisioning` wrote
`<brand_id>-<flow_id>` while `lib.worker_labels` hardcoded `dogfood-`. A
re-provision therefore created a SECOND row per flow, the dispatcher opened a
run under one id while the script closed the other, and the UI's row stayed
`running` forever. For any second brand every engager run would have reported
under one brand's hardcoded prefix.

Additive where it can be: a legacy row whose brand-derived twin ALREADY exists
is not deleted, it is retired the way `dogfood-fb-scanner` was (cron moved to
`cron_disabled`, `disabled_reason` recorded) and the twin is re-armed with the
cron the legacy row was actually running. Only a legacy row with no twin is
renamed in place, which preserves the row and its history rather than copying
it.

    python scripts/migrate_task_ids_to_brand.py --dry-run
    python scripts/migrate_task_ids_to_brand.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from psycopg.types.json import Jsonb

from lib import db
from lib.worker_labels import LEGACY_TASK_ID_PREFIX

RETIRE_REASON = (
    "superseded by the brand-derived task id for the same flow (migration "
    "2026-08-31). Two ids for one flow split its worker_runs history and left "
    "the row the UI reads stuck at 'running'."
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    legacy = db.fetch_all(
        "SELECT id, brand_id, schedule FROM schedule_tasks WHERE id LIKE %(p)s ORDER BY id",
        {"p": f"{LEGACY_TASK_ID_PREFIX}%"},
    )
    if not legacy:
        print("nothing to migrate")
        return 0

    renames: list[tuple[str, str]] = []
    merges: list[tuple[str, str, str | None]] = []
    for row in legacy:
        old_id = row["id"]
        flow_id = old_id[len(LEGACY_TASK_ID_PREFIX) :]
        new_id = f"{row['brand_id']}-{flow_id}"
        if old_id == new_id:
            continue
        twin = db.fetch_one("SELECT id FROM schedule_tasks WHERE id = %(i)s", {"i": new_id})
        cron = (row["schedule"] or {}).get("cron")
        (merges if twin else renames).append((old_id, new_id, cron))

    print(f"legacy rows        : {len(legacy)}")
    print(f"rename in place    : {len(renames)}")
    print(f"merge into twin    : {len(merges)}")
    for old_id, new_id, cron in merges:
        print(f"   merge  {old_id:<32} -> {new_id}  (cron '{cron}' moves to the twin)")
    for old_id, new_id, _ in renames[:5]:
        print(f"   rename {old_id:<32} -> {new_id}")
    if len(renames) > 5:
        print(f"   ... and {len(renames) - 5} more renames")

    if args.dry_run:
        print("\ndry-run: nothing written")
        return 0

    for old_id, new_id, cron in merges:
        # Re-arm the twin with the cron the legacy row was actually running...
        twin = db.fetch_one("SELECT schedule FROM schedule_tasks WHERE id = %(i)s", {"i": new_id})
        sched = dict(twin["schedule"] or {})
        sched.pop("cron_disabled", None)
        sched.pop("disabled_reason", None)
        if cron:
            sched["cron"] = cron
        db.execute(
            "UPDATE schedule_tasks SET schedule = %(s)s WHERE id = %(i)s",
            {"s": Jsonb(sched), "i": new_id},
        )
        # ...then retire the legacy row rather than deleting it.
        old = db.fetch_one("SELECT schedule FROM schedule_tasks WHERE id = %(i)s", {"i": old_id})
        old_sched = dict(old["schedule"] or {})
        if "cron" in old_sched:
            old_sched["cron_disabled"] = old_sched.pop("cron")
        old_sched["disabled_reason"] = RETIRE_REASON
        db.execute(
            "UPDATE schedule_tasks SET schedule = %(s)s WHERE id = %(i)s",
            {"s": Jsonb(old_sched), "i": old_id},
        )
        print(f"merged {old_id} -> {new_id}")

    for old_id, new_id, _ in renames:
        db.execute(
            "UPDATE schedule_tasks SET id = %(new)s WHERE id = %(old)s",
            {"new": new_id, "old": old_id},
        )
        # Carry the run history across so the UI doesn't report "never ran".
        db.execute(
            "UPDATE worker_runs SET worker_label = %(new)s WHERE worker_label = %(old)s "
            "AND NOT EXISTS (SELECT 1 FROM worker_runs w2 WHERE w2.worker_label = %(new)s "
            "AND w2.brand = worker_runs.brand)",
            {"new": new_id, "old": old_id},
        )
        print(f"renamed {old_id} -> {new_id}")

    print("\nmigration complete -- re-run tools.profiles_build to refresh plists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
