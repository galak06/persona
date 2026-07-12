# pyright: reportMissingImports=false
"""Flow-readiness + "Run Now" API (`GET /brands/{id}/flows`,
`POST /brands/{id}/flows/{flow_id}/run`) -- split out of `brands_api.py`/
`brand_settings_api.py` to keep those files under the project's 300-line
limit; same router/prefix, registered as a third brands router in
`approval_api.py`.

`GET .../flows` answers the earlier open UX question -- "how does the
operator know fb-group-scout needs to run (and its groups approved) before
fb-scanner has anything to scan" -- by surfacing per-flow last-run status
and a readiness signal (see `lib/flow_readiness.py`) instead of leaving a
0-groups/0-hashtags brand silently doing nothing.

`POST .../run` enqueues directly onto the same `flow-run` Redis queue
`scripts/task_dispatcher.py` (the producer) and `scripts/task_worker.py`
(the consumer) already share -- bypassing the cron-due check entirely, but
still respecting `enabled_flows` (a disabled flow can't be run from here
either; the settings page is the one place to re-enable it).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from api.brand_schemas import FlowStatus, FlowStatusResponse, RunNowResponse
from lib import brands_db, schedule_db
from lib.brands_db.models import MANAGED_FLOW_IDS
from lib.flow_readiness import flow_status
from lib.task_queue import TaskQueue

router = APIRouter()

_QUEUE_WORKER = "flow-run"  # must match scripts/task_dispatcher.py's QUEUE_WORKER
_DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 600


@router.get("/brands/{brand_id}/flows", response_model=FlowStatusResponse)
def get_flow_status(brand_id: str) -> FlowStatusResponse:
    """Per-managed-flow enabled state, last-run status, and readiness signal.

    404 if the brand row doesn't exist.
    """
    row = brands_db.get(brand_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"brand '{brand_id}' not found")

    brand_dir = Path(str(row.get("brand_dir") or ""))
    raw_flows = flow_status(
        brand_id=brand_id,
        brand_dir=brand_dir,
        enabled_flows=list(row.get("enabled_flows") or []),
    )
    flows = [FlowStatus(**f) for f in raw_flows]
    return FlowStatusResponse(brand_id=brand_id, flows=flows)


@router.post("/brands/{brand_id}/flows/{flow_id}/run", response_model=RunNowResponse)
def run_flow_now(brand_id: str, flow_id: str) -> RunNowResponse:
    """Enqueue one flow's `schedule_tasks` row onto the `flow-run` queue now.

    404 if the brand row doesn't exist, or if `flow_id` has no provisioned
    `schedule_tasks` row for this brand (e.g. `fb-group-scout` was never
    enabled). 409 if the flow is a managed flow currently disabled in
    `enabled_flows` -- re-enable it in settings first, same rule
    `scripts/task_dispatcher.py`'s own gate enforces for scheduled runs.
    """
    row = brands_db.get(brand_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"brand '{brand_id}' not found")

    schedule_task_id = f"{brand_id}-{flow_id}"
    task = next(
        (t for t in schedule_db.load_all() if t.get("id") == schedule_task_id),
        None,
    )
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"flow '{flow_id}' is not provisioned for brand '{brand_id}'",
        )

    enabled_flows = set(row.get("enabled_flows") or [])
    if flow_id in MANAGED_FLOW_IDS and flow_id not in enabled_flows:
        raise HTTPException(
            status_code=409,
            detail=f"flow '{flow_id}' is disabled for brand '{brand_id}' — enable it in settings first",
        )

    brand_dir = str(row.get("brand_dir") or "")
    timeout_minutes = task.get("timeout_minutes")
    timeout_seconds = (
        int(timeout_minutes) * 60 if timeout_minutes else _DEFAULT_SUBPROCESS_TIMEOUT_SECONDS
    )
    payload = {
        "schedule_task_id": schedule_task_id,
        "script": task["script"],
        "args": [str(a) for a in (task.get("args") or [])],
        "brand": brand_id,
        "brand_dir": brand_dir,
        "timeout_seconds": timeout_seconds,
    }
    TaskQueue(worker=_QUEUE_WORKER, brand=brand_id).push(payload)

    return RunNowResponse(brand_id=brand_id, flow_id=flow_id, schedule_task_id=schedule_task_id)
