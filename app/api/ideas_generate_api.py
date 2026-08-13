"""Trigger the CrewAI content scout (Trends + Idea crews) from the frontend.

  POST /api/v1/ideas/generate         — dispatch a scout run to the worker
  GET  /api/v1/ideas/generate/status  — is one queued/running / how did it end

**The run executes on the worker, not here.** The scout's Trends stage scrapes
the public Instagram trends feed through Playwright. `Dockerfile.api` installs
the `playwright` Python package (it arrives with the full `requirements.txt`)
but never runs `playwright install`, so `chromium.launch()` fails on a missing
binary in this container while the driver itself imports and starts fine.

That half-install was not survivable. The failed launch left Playwright's
event loop running in the process, and the installed crewai (1.15.14) refuses
a synchronous `Crew.kickoff()` while a loop is running -- so the Trends crew
died, the Idea crew never ran, and `run_crew_scout` returned `[]`. The scout
then exited 0, this module recorded `ok: true`, and the button reported
success over a run that wrote nothing. Zero ideas were inserted between
2026-08-06 and the cutover. `lib/sessions/browser.py` no longer leaks the loop
on a failed launch, but the real fix is running the scout where the browser
actually exists: `persona-worker-1` is built with
`playwright install --with-deps chromium` (see `Dockerfile.worker`).

So this module now *dispatches* instead of executing: it pushes a `flow-run`
item onto the same Redis queue `scripts/task_dispatcher.py` and
`POST /brands/{id}/flows/{flow_id}/run` use, and `scripts/task_worker.py`
picks it up, runs the scout with the brand's own environment, and records the
outcome in `worker_runs`. Same dispatch shape `reels_compose_api` uses, for
the same reason (that one needs ffmpeg and fonts the API image also lacks).

Status therefore reads that shared `worker_runs` row rather than any
in-process global -- the API no longer owns the run, may not be the process
that started it, and can restart mid-run without losing it.

Concurrency: one run at a time, enforced against the shared row (a run already
`queued`/`running` gets 409) under a local mutex that makes the
check-then-enqueue atomic within this process. The scout is expensive (Serper
searches + two DeepSeek crews) and self-deduplicating against existing topics,
so two concurrent runs would race that dedup check and write duplicates.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.brand_context import resolve_api_brand
from lib import worker_db
from lib.task_queue import TaskQueue

# Serper + two crews; generous, not unbounded. Passed explicitly because the
# worker's own default (600s) is tighter than this run needs.
_SCOUT_TIMEOUT_SECONDS = 900
_SCOUT_SCRIPT = "scripts/crewai_content_scout.py"
# Without --apply the scout is a dry run and writes nothing.
_SCOUT_ARGS = ["--apply"]
# Must match scripts/task_worker.py's QUEUE_WORKER -- same Redis list.
_QUEUE_WORKER = "flow-run"
_FLOW_ID = "content-scout"

# Statuses that mean "a run is already in flight". `queued` is written here at
# dispatch so a second click is refused before the worker has picked the item
# up; the worker overwrites it with `running`, then the terminal status.
_QUEUED = "queued"
_RUNNING = "running"
_IN_FLIGHT = frozenset({_QUEUED, _RUNNING})

log = logging.getLogger(__name__)

router = APIRouter(tags=["ideas"])

# Makes check-then-enqueue atomic within this process. NOT the run's lifetime
# lock (the run outlives this request, in another container) -- the shared
# `worker_runs` row is the real single-flight guard.
_dispatch_lock = threading.Lock()


class GenerateStatus(BaseModel):
    running: bool
    started_at: str | None = None
    finished_at: str | None = None
    ok: bool | None = None
    detail: str | None = None


def _label(brand_id: str) -> str:
    """`worker_runs.worker_label` for this flow.

    Prefixed with the brand so `task_worker._write_flow_log` strips it back to
    `content-scout` and writes `logs/cron_content_scout.log`, matching the
    convention the Explorer page's log viewer already expects.
    """
    return f"{brand_id}-{_FLOW_ID}"


@router.post("/ideas/generate", status_code=202)
def generate_ideas() -> dict[str, str]:
    """Dispatch one scout run to the worker. 202 immediately; poll
    /ideas/generate/status."""
    brand_id, brand_dir = resolve_api_brand()
    label = _label(brand_id)

    with _dispatch_lock:
        current = worker_db.get_one(brand_dir, label, brand_id)
        if current is not None and str(current.get("status")) in _IN_FLIGHT:
            raise HTTPException(status_code=409, detail="a generation run is already in progress")

        # Recorded BEFORE the push so the status endpoint reports the run the
        # moment this returns, rather than a gap where it looks idle until the
        # worker's own record_start lands.
        worker_db.record_start(brand_dir, label, brand_id)
        payload: dict[str, Any] = {
            "schedule_task_id": label,
            "script": _SCOUT_SCRIPT,
            "args": list(_SCOUT_ARGS),
            "brand": brand_id,
            "brand_dir": brand_dir,
            "timeout_seconds": _SCOUT_TIMEOUT_SECONDS,
        }
        TaskQueue(worker=_QUEUE_WORKER, brand=brand_id).push(payload)

    log.info(json.dumps({"event": "ideas_generate_dispatched", "brand": brand_id, "label": label}))
    return {"status": "started"}


@router.get("/ideas/generate/status", response_model=GenerateStatus)
def generate_status() -> GenerateStatus:
    """State of the current/last run, for the frontend's polling loop.

    Read from the shared `worker_runs` row, so it reflects a run executing in
    the worker container (and survives an API restart mid-run).
    """
    brand_id, brand_dir = resolve_api_brand()
    row = worker_db.get_one(brand_dir, _label(brand_id), brand_id)
    if row is None:
        return GenerateStatus(running=False)

    status = str(row.get("status") or "")
    last_run = row.get("last_run")
    last_run_str = str(last_run) if last_run else None
    if status in _IN_FLIGHT:
        # Queued counts as in-progress: the user clicked, work is coming.
        return GenerateStatus(running=True, started_at=last_run_str)

    return GenerateStatus(
        running=False,
        finished_at=last_run_str,
        ok=status == "success",
        detail=str(row.get("message") or "")[-500:],
    )
