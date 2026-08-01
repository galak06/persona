"""Register an already-provisioned brand in Postgres (idempotent, additive).

Why this exists: brands onboarded before `lib/schedule_db.py` moved from
SQLite to Postgres are complete on disk (config.json, .env, sessions, logs)
but have zero rows in the `brands` and `schedule_tasks` tables. Three visible
symptoms, one cause:

  * `GET /api/v1/brands` returns `[]`, so the UI brand picker falls back to
    the literal string in `BrandContext.tsx`'s `FALLBACK_BRAND`.
  * `scripts/task_dispatcher.py` has no tasks to dispatch.
  * `scripts/regenerate_plists.py` prints "schedule.json has no tasks".

Both tables are otherwise written only by `lib/brand_provisioning.py`, and
re-running full provisioning on an EXISTING brand rewrites `config.json` from
a `BrandSpec` -- losing any live hand-tuning the spec doesn't reproduce. This
script therefore reads what is already on disk and writes only the two DB
rows, touching no files.

Usage::

    python scripts/backfill_brand_registration.py --dry-run
    python scripts/backfill_brand_registration.py --apply
    python scripts/backfill_brand_registration.py --apply --brand-dir /path/to/brand

Exit codes: 0 success, 1 failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from lib import schedule_db
from lib.brands_db.models import BrandStatus
from lib.brands_db.repository import BrandsRepository
from lib.config import default_brand_dir
from lib.local_env import get_group_join_limit, get_runtime_headless


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    return data if isinstance(data, dict) else {}


def enabled_flows_for(brand_dir: Path, only: list[str] | None = None) -> list[str]:
    """Which flows this brand may dispatch.

    Derived from the brand's own `schedule.json` — the flows it is actually
    configured to run — rather than from the new-brand default. `only`
    overrides it (`--enable a,b`) for the common case of deliberately keeping
    a narrow scope while the rest of the pipeline is being brought up.
    """
    if only is not None:
        return list(only)
    tasks = _load_json(brand_dir / "schedule.json").get("tasks", [])
    return [str(t["id"]).removeprefix("dogfood-") for t in tasks if t.get("id")]


def build_brand_row(brand_dir: Path, enabled_flows: list[str] | None = None) -> dict[str, Any]:
    """Derive the `brands` row from files already on disk.

    `config.json`'s `site` block is authoritative (it is what every runtime
    consumer reads); `brand.json` only supplies the runtime overlay flags.
    """
    site = _load_json(brand_dir / "config.json").get("site", {})
    if not site:
        raise SystemExit(
            f"no `site` block in {brand_dir / 'config.json'} — cannot build a brand row"
        )

    enabled = enabled_flows_for(brand_dir, enabled_flows)

    return {
        "enabled_flows": enabled,
        "brand_id": brand_dir.name,
        "name": site.get("name") or brand_dir.name,
        "site_url": site.get("url", ""),
        "niche": site.get("niche", ""),
        "persona": site.get("brand_persona", ""),
        "mascot_name": site.get("mascot_name", ""),
        "target_audience": site.get("target_audience", ""),
        "headless": get_runtime_headless(),
        "group_join_limit": get_group_join_limit(),
        "status": BrandStatus.ACTIVE,
        "brand_dir": str(brand_dir),
    }


def build_schedule_rows(brand_dir: Path, brand_id: str) -> list[dict[str, Any]]:
    """Read `<brand_dir>/schedule.json` into `schedule_tasks` row dicts.

    Two shape fixes the JSON needs before it can be stored:
      * `order` -> `order_num` (the column name; `_load_from_db` maps it back).
        Left alone it would silently spill into `extra` and leave order_num NULL.
      * `brand_id` is NOT NULL in the table but absent from the JSON, which
        predates multi-brand support.
    """
    schedule_path = brand_dir / "schedule.json"
    tasks = _load_json(schedule_path).get("tasks", [])
    if not tasks:
        raise SystemExit(f"no tasks found in {schedule_path}")

    rows: list[dict[str, Any]] = []
    for task in tasks:
        row = dict(task)
        row["order_num"] = row.pop("order", 0)
        row["brand_id"] = brand_id
        row.setdefault("title", str(row.get("id", "")).removeprefix("dogfood-"))
        rows.append(row)
    return rows


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--brand-dir",
        type=Path,
        default=None,
        help="brand folder (default: $BRAND_DIR, else inferred)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="print what would be written")
    group.add_argument("--apply", action="store_true", help="write the rows")
    parser.add_argument(
        "--enable",
        default=None,
        help="comma-separated flow ids to enable (default: every flow in schedule.json)",
    )
    parser.add_argument(
        "--update-enabled-flows",
        action="store_true",
        help="also correct enabled_flows on an ALREADY-registered brand",
    )
    return parser.parse_args()


def _requested_flows(enable: str | None) -> list[str] | None:
    """Flow ids from a comma-separated --enable value, or None for all."""
    if not enable:
        return None
    return [flow.strip() for flow in enable.split(",") if flow.strip()]


@dataclass(frozen=True)
class BackfillPlan:
    """What a run would write, resolved before anything is written."""

    brand_dir: Path
    brand_row: dict[str, Any]
    schedule_rows: list[dict[str, Any]]
    already_registered: bool
    existing_task_count: int

    @property
    def brand_id(self) -> str:
        return str(self.brand_row["brand_id"])

    @property
    def enabled_flows(self) -> list[str]:
        return list(self.brand_row["enabled_flows"])


def _print_plan(plan: BackfillPlan) -> None:
    """Show what will be written before anything is."""
    verb = "already present, will skip" if plan.already_registered else "will INSERT"
    print(f"brand dir      : {plan.brand_dir}")
    print(f"brands row     : {plan.brand_id} ({plan.brand_row['name']}) — {verb}")
    print(
        f"schedule_tasks : {len(plan.schedule_rows)} in schedule.json, "
        f"{plan.existing_task_count} already in DB — upsert by id"
    )
    print(f"enabled_flows  : {len(plan.enabled_flows)} — {plan.enabled_flows}")


def _write_rows(repo: BrandsRepository, plan: BackfillPlan, update_enabled_flows: bool) -> None:
    """Insert the brand row if new, correct its flows if asked, upsert the schedule."""
    if not plan.already_registered:
        repo.create(**plan.brand_row)
        print(f"inserted brands row: {plan.brand_id}")
    elif update_enabled_flows:
        repo.update(plan.brand_id, enabled_flows=plan.enabled_flows)
        print(f"updated enabled_flows -> {plan.enabled_flows}")

    for row in plan.schedule_rows:
        schedule_db.save_task(None, row)
    print(f"upserted {len(plan.schedule_rows)} schedule_tasks rows")


def main() -> int:
    args = _parse_args()

    brand_dir = (args.brand_dir or default_brand_dir()).resolve()
    if not (brand_dir / "config.json").is_file():
        print(f"ERROR: no config.json under {brand_dir}", file=sys.stderr)
        return 1

    brand_row = build_brand_row(brand_dir, _requested_flows(args.enable))
    repo = BrandsRepository()
    plan = BackfillPlan(
        brand_dir=brand_dir,
        brand_row=brand_row,
        schedule_rows=build_schedule_rows(brand_dir, brand_row["brand_id"]),
        already_registered=repo.get(brand_row["brand_id"]) is not None,
        existing_task_count=len(schedule_db.load_all()),
    )

    _print_plan(plan)

    if not args.apply:
        print("\n(dry run — nothing written; pass --apply to write)")
        return 0

    _write_rows(repo, plan, args.update_enabled_flows)
    print(
        f"\nverify: brands={len(repo.list_brands())}, schedule_tasks={len(schedule_db.load_all())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
