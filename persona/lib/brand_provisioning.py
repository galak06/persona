"""Brand onboarding orchestration: folder + config + schedule_tasks rows.

Pairs with `lib/brand_templates.py` (pure rendering) -- this module is the
I/O side: computes `brands/<slug>/`, writes the three rendered files, and
inserts the brand's `ig-scanner`/`fb-scanner` `schedule_tasks` rows via
`lib/schedule_db.py` so PR2's `scripts/task_dispatcher.py` picks them up.

Deliberately does NOT import `lib.config`/`lib.bootstrap` (or anything that
transitively does): those modules load a brand's `config.json` as a
module-level singleton keyed off `BRAND_DIR`, which is exactly the file this
module is about to create for a brand that doesn't have one yet. Onboarding
must work standalone, regardless of whatever brand (if any) the current
process's `BRAND_DIR` happens to point at.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib import schedule_db
from lib.brand_templates import (
    BrandSpec,
    render_brand_facts_md,
    render_brand_json,
    render_config_json,
    render_instagram_hashtags_csv,
)
from lib.brands_db.models import BrandStatus
from lib.brands_db.repository import BrandsRepository
from lib.groups_db.models import slugify

# persona/ (this file lives at persona/lib/brand_provisioning.py).
_PERSONA_ROOT = Path(__file__).resolve().parent.parent

# Sibling-directory convention already established by the merged Phase 0/A
# infra: docker-compose.yml (persona/docker-compose.yml) mounts `./brands` as
# `api`'s brands root, and `persona/brands/` already exists on disk (empty,
# provisioned ready) as of this PR. NOT a repo-root-of-the-outer-monorepo
# path -- `persona/` is this engine's own repo root (the eventual extraction
# target for the open-source project described in the plan), so "sibling to
# the repo root" resolves to a sibling of `persona/lib/`'s parent, i.e.
# `persona/brands/`.
BRANDS_ROOT = _PERSONA_ROOT / "brands"

_PROFILES_DIR = _PERSONA_ROOT / "profiles"

# (profile file, flow id) for every flow this stage of onboarding knows how
# to provision. Cron strings are read from these files at provision time
# rather than hardcoded, so a profile change is automatically picked up by
# the next onboarding run instead of silently drifting out of sync. Not
# every brand gets every row -- `_build_stage1_tasks` filters this list down
# to `spec.enabled_flows` (default: just the first two -- see
# `default_enabled_flows()`), so a brand that never opts into
# `fb-group-scout` never gets that `schedule_tasks` row at all.
_STAGE1_FLOWS: tuple[tuple[str, str], ...] = (
    ("instagram.json", "ig-scanner"),
    ("facebook.json", "fb-scanner"),
    ("facebook.json", "fb-group-scout"),
)


@dataclass
class ProvisionResult:
    """What `provision_brand` did (or, under `dry_run`, would do)."""

    brand_id: str
    brand_dir: Path
    files_written: list[str] = field(default_factory=list)
    schedule_tasks_created: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _load_flow(profile_filename: str, flow_id: str) -> dict[str, Any]:
    """Read one `flows[]` entry (by id) out of a `profiles/*.json` file."""
    profile_path = _PROFILES_DIR / profile_filename
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    for flow in data.get("flows", []):
        if flow.get("id") == flow_id:
            return dict(flow)
    raise RuntimeError(f"flow '{flow_id}' not found in {profile_path}")


def _flow_to_task(flow: dict[str, Any], *, brand_id: str) -> dict[str, Any]:
    """One `schedule_tasks` row from a `profiles/*.json` flow definition.

    `id` is brand-prefixed (`<brand_id>-<flow_id>`) so multiple brands'
    dispatcher rows never collide. `depends_on`/`inputs` are cleared even
    when the source flow has them (e.g. `ig-scanner` depends on
    `site-analyzer`) -- onboarding only ever provisions flows from
    `_STAGE1_FLOWS`, so a dangling dependency on a flow this brand doesn't
    have would be misleading. `task_dispatcher.py` doesn't currently
    evaluate `depends_on` at all, so this is a data-hygiene choice, not a
    behavior change. `requires_browser` is forced `true` per the plan's B3
    spec regardless of the source flow's value (`ig-scanner`/`fb-scanner`/
    `fb-group-scout` are all Playwright-driven, so this is always accurate
    for every id `_STAGE1_FLOWS` can name).
    """
    flow_id = str(flow["id"])
    return {
        "id": f"{brand_id}-{flow_id}",
        "brand_id": brand_id,
        "title": flow_id,
        "description": flow.get("description", ""),
        "order_num": flow.get("order", 0),
        "script": flow["script"],
        "skill": flow.get("skill", ""),
        "args": [],
        "timeout_minutes": None,
        "depends_on": [],
        "requires_approval": bool(flow.get("requires_approval", False)),
        "requires_browser": True,
        "re_run_guard": bool(flow.get("re_run_guard", True)),
        "output_file": flow.get("output_file"),
        "schedule": {"cron": flow["schedule"]["cron"]},
        "inputs": [],
        "telegram_notify": bool(flow.get("telegram_notify", True)),
        "extra": {},
    }


def _build_stage1_tasks(brand_id: str, enabled_flows: list[str]) -> list[dict[str, Any]]:
    """Build one `schedule_tasks` row per `_STAGE1_FLOWS` entry the brand
    actually opted into. A flow id absent from `enabled_flows` is skipped
    entirely -- no row is ever inserted for it -- rather than inserted and
    later filtered at dispatch time, so `GET .../schedule` never lists a
    task the brand can't run.
    """
    return [
        _flow_to_task(_load_flow(profile_filename, flow_id), brand_id=brand_id)
        for profile_filename, flow_id in _STAGE1_FLOWS
        if flow_id in enabled_flows
    ]


def provision_brand(spec: BrandSpec, *, dry_run: bool = False) -> ProvisionResult:
    """Scaffold `brands/<slug>/` + insert its `schedule_tasks` rows.

    Idempotent: re-running only rewrites the files this function owns
    (config.json, brand_facts.md, instagram_accounts.csv, brand.json) and
    re-upserts the same `schedule_tasks` rows for `spec.enabled_flows` --
    never deletes anything, never errors just because the folder or brand
    row already exists. Enabling a previously-disabled flow (e.g. via a
    settings edit) creates its row the next time this runs; disabling one
    leaves its existing row in place -- `scripts/task_dispatcher.py` is what
    actually gates execution on `enabled_flows` at dispatch time, since a
    once-enabled flow's row is never deleted by this function.

    `dry_run=True` renders everything and returns the same `ProvisionResult`
    shape the real run would, but performs no writes to disk or the DB
    (reading the static `profiles/*.json` cron definitions is the only I/O).
    """
    slug = slugify(spec.name)
    if not slug:
        raise ValueError(f"brand name {spec.name!r} does not yield a usable slug")

    brand_dir = BRANDS_ROOT / slug

    config = render_config_json(spec)
    brand_facts_md = render_brand_facts_md(spec)
    hashtags_csv = render_instagram_hashtags_csv(spec)
    brand_json = render_brand_json(spec)
    tasks = _build_stage1_tasks(slug, spec.enabled_flows)

    warnings: list[str] = []
    hashtags_csv_exists = (brand_dir / "data" / "config" / "instagram_accounts.csv").exists()
    if not spec.primary_keywords and not spec.secondary_keywords and not hashtags_csv_exists:
        warnings.append(
            "No primary_keywords or secondary_keywords supplied — "
            "comment_generator relevance scoring and the Instagram hashtag "
            "scan list will start empty for this brand."
        )

    files_written = [
        "config.json",
        "data/config/brand_facts.md",
        "data/config/instagram_accounts.csv",
        "brand.json",
    ]
    schedule_tasks_created = [str(t["id"]) for t in tasks]

    if dry_run:
        return ProvisionResult(
            brand_id=slug,
            brand_dir=brand_dir,
            files_written=files_written,
            schedule_tasks_created=schedule_tasks_created,
            warnings=warnings,
        )

    # Only these three directories -- session files, dedup caches, and
    # queues are lazily created by ig_scan.py/fb_scan.py themselves on first
    # write (confirmed by reading both scripts). No data/db/: everything is
    # Postgres now.
    (brand_dir / "data" / "config").mkdir(parents=True, exist_ok=True)
    (brand_dir / "state").mkdir(parents=True, exist_ok=True)
    (brand_dir / "logs").mkdir(parents=True, exist_ok=True)

    (brand_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    (brand_dir / "data" / "config" / "brand_facts.md").write_text(brand_facts_md, encoding="utf-8")
    # instagram_accounts.csv is NOT re-rendered on an already-provisioned
    # brand: unlike config.json (a pure function of `spec`, safe to
    # regenerate), an operator may have hand-curated this file with
    # hashtags no `primary_keywords`/`secondary_keywords` mechanically
    # derive (dogfoodandfun's real 26-hashtag list has none of its rows
    # traceable to any keyword). Overwriting it here wiped that file down
    # to a header-only stub on every settings edit, silently breaking
    # ig-scanner (0 sources to scan, but still exits 0 -- no error, no
    # crash, just quietly doing nothing) until the file was manually
    # restored from a backup.
    hashtags_csv_path = brand_dir / "data" / "config" / "instagram_accounts.csv"
    if not hashtags_csv_path.exists():
        hashtags_csv_path.write_text(hashtags_csv, encoding="utf-8")
    # brand.json is a SHALLOW MERGE onto any existing file, not an overwrite:
    # render_brand_json() only ever computes `runtime`/`group_discovery` --
    # those two keys must always reflect the latest settings-page edit, but
    # an operator may have hand-added other top-level keys (`profiles.*
    # .rate_limits` overrides, `campaign`, `brand` identity fields) that
    # render_brand_json() never owns and was never asked to preserve. A full
    # overwrite here silently dropped all of that on every settings edit --
    # same class of bug the instagram_accounts.csv fix above addresses.
    brand_json_path = brand_dir / "brand.json"
    existing_brand_json: dict[str, Any] = {}
    if brand_json_path.exists():
        try:
            existing_brand_json = json.loads(brand_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing_brand_json = {}
    merged_brand_json = {**existing_brand_json, **brand_json}
    brand_json_path.write_text(json.dumps(merged_brand_json, indent=2) + "\n", encoding="utf-8")

    for task in tasks:
        schedule_db.save_task(None, task)

    repo = BrandsRepository()
    if repo.get(slug) is None:
        repo.create(
            brand_id=slug,
            name=spec.name,
            site_url=spec.site_url,
            niche=spec.niche,
            persona=spec.brand_persona,
            mascot_name=spec.mascot_name,
            target_audience=spec.target_audience,
            keywords={
                "primary_keywords": list(spec.primary_keywords),
                "secondary_keywords": list(spec.secondary_keywords),
                "competitor_mentions": list(spec.competitor_mentions),
            },
            competitor_accounts=list(spec.competitor_accounts),
            enabled_flows=list(spec.enabled_flows),
            headless=spec.headless,
            group_join_limit=spec.group_join_limit,
            brand_dir=str(brand_dir),
        )
    repo.set_brand_dir(slug, str(brand_dir))
    repo.update_status(slug, BrandStatus.PROVISIONED)

    return ProvisionResult(
        brand_id=slug,
        brand_dir=brand_dir,
        files_written=files_written,
        schedule_tasks_created=schedule_tasks_created,
        warnings=warnings,
    )
