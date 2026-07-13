"""Per-flow readiness signals + last-run status for the brand settings UI.

Answers the operator question "how do I know fb-group-scout needs to run
(and its candidates approved) before fb-scanner has anything to scan" --
today that's silent (0 groups joined -> fb-scanner just finds nothing, with
no UI signal explaining why). `flow_status()` surfaces, per managed flow:
whether it's enabled, its last `worker_runs` status, and a flow-specific
readiness count (joined-group count for the two Facebook flows, hashtag
count for ig-scanner).

Queries `fb_groups` directly (not via `lib.groups_db`'s repository, which
resolves its brand implicitly from the CURRENT process's `BRAND_DIR` env
var) because this module must report on an arbitrary `brand_id` passed in
by the API layer -- not necessarily whichever brand the `api` container's
own `BRAND_DIR`/`PERSONA_BRAND` happens to be pinned to.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from lib import db, worker_db

# Presentation order (onboarding order), not `MANAGED_FLOW_IDS`'s frozenset
# iteration order.
_FLOW_ORDER: tuple[str, ...] = ("ig-scanner", "fb-scanner", "fb-group-scout")
_FLOW_SCRIPTS: dict[str, str] = {
    "ig-scanner": "scripts/ig_scan.py",
    "fb-scanner": "scripts/fb_scan.py",
    "fb-group-scout": "scripts/fb_group_scout.py",
}


def _joined_group_count(brand_id: str) -> int:
    row = db.fetch_one(
        "SELECT count(*) AS n FROM fb_groups WHERE brand_id = %s AND status = 'joined'",
        (brand_id,),
    )
    return int(row["n"]) if row else 0


def _hashtag_count(brand_dir: Path) -> int:
    csv_path = brand_dir / "data" / "config" / "instagram_accounts.csv"
    if not csv_path.exists():
        return 0
    with csv_path.open(encoding="utf-8", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def _facebook_readiness(brand_id: str, *, no_groups_hint: str) -> dict[str, Any]:
    count = _joined_group_count(brand_id)
    return {
        "signal": "joined_groups",
        "count": count,
        "ready": count > 0,
        "hint": no_groups_hint if count == 0 else f"{count} group(s) joined.",
    }


def _readiness_for(flow_id: str, *, brand_id: str, brand_dir: Path) -> dict[str, Any]:
    if flow_id == "fb-group-scout":
        return _facebook_readiness(
            brand_id,
            no_groups_hint=(
                "No groups joined yet — the first fb-group-scout run finds "
                "candidates; approve them in the Inbox to actually join."
            ),
        )
    if flow_id == "fb-scanner":
        return _facebook_readiness(
            brand_id,
            no_groups_hint=(
                "No groups joined yet — fb-scanner has nothing to scan "
                "until fb-group-scout finds and joins some."
            ),
        )
    if flow_id == "ig-scanner":
        count = _hashtag_count(brand_dir)
        return {
            "signal": "hashtags",
            "count": count,
            "ready": count > 0,
            "hint": (
                "No hashtags configured — add primary/secondary keywords in "
                "settings to give ig-scanner something to scan."
                if count == 0
                else f"{count} hashtag(s) configured."
            ),
        }
    return {
        "signal": None,
        "count": None,
        "ready": True,
        "hint": "",
    }  # pragma: no cover -- unreachable for MANAGED_FLOW_IDS


def flow_status(
    *, brand_id: str, brand_dir: Path, enabled_flows: list[str]
) -> list[dict[str, Any]]:
    """One entry per managed flow: enabled?, last-run status, readiness signal."""
    enabled_set = set(enabled_flows)
    out: list[dict[str, Any]] = []
    for flow_id in _FLOW_ORDER:
        schedule_task_id = f"{brand_id}-{flow_id}"
        last_run = worker_db.get_one(brand_dir, schedule_task_id, brand_id)
        out.append(
            {
                "flow_id": flow_id,
                "script": _FLOW_SCRIPTS[flow_id],
                "enabled": flow_id in enabled_set,
                "last_run": (
                    {
                        "status": last_run["status"],
                        "last_run": last_run["last_run"],
                        "message": last_run["message"],
                    }
                    if last_run
                    else None
                ),
                "readiness": _readiness_for(flow_id, brand_id=brand_id, brand_dir=brand_dir),
            }
        )
    return out
