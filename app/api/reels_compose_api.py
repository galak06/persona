"""Trigger the CrewAI reels pipeline from the frontend.

  POST /api/v1/reels/compose         — dispatch a compose run to the worker
  GET  /api/v1/reels/compose/status  — is one queued/running / how did it end

**The run executes on the worker, not here.** Composing a reel needs ffmpeg
and real fonts; `Dockerfile.api` deliberately ships neither ("video
composition happens on the host"), so running the pipeline as an API
subprocess died on `OSError: cannot open resource` from PIL's font loader
and never produced a reel. `persona-worker-1` already has both, so this
module now *dispatches* instead of executing: it pushes a `flow-run` item
onto the same Redis queue `scripts/task_dispatcher.py` and
`POST /brands/{id}/flows/{flow_id}/run` use, and `scripts/task_worker.py`
picks it up, runs the pipeline with the brand's own environment, and records
the outcome in `worker_runs`.

Status therefore reads that shared `worker_runs` row rather than any
in-process global -- the API process no longer owns the run and may not even
be the process that started it.

Concurrency: one run at a time, enforced against the shared row (a run
already `queued`/`running` gets 409) under a local mutex that makes the
check-then-enqueue atomic within this process.
"""

from __future__ import annotations

import json
import logging
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.brand_context import resolve_api_brand
from lib import flow_queue, worker_db

# Per idea: crew + image generation + two ffmpeg renders. A five-idea batch
# runs well past the scout's 900s, so this ceiling is wider.
_COMPOSE_TIMEOUT_SECONDS = 1800
_PIPELINE_SCRIPT = "scripts/crewai_reels_pipeline.py"
# Must match scripts/task_worker.py's QUEUE_WORKER -- same Redis list.
_FLOW_ID = "reels-compose"

# Statuses that mean "a run is already in flight". `queued` is written here at
# dispatch so a second click is refused before the worker has picked the item
# up; the worker overwrites it with `running`, then the terminal status.
_QUEUED = "queued"
_RUNNING = "running"
_IN_FLIGHT = frozenset({_QUEUED, _RUNNING})

log = logging.getLogger(__name__)

router = APIRouter(tags=["reels"])

# Makes check-then-enqueue atomic within this process. NOT the run's lifetime
# lock (the run outlives this request, in another container) -- the shared
# `worker_runs` row is the real single-flight guard.
_dispatch_lock = threading.Lock()


class ComposeStatus(BaseModel):
    running: bool
    started_at: str | None = None
    finished_at: str | None = None
    ok: bool | None = None
    detail: str | None = None
    # Purely informational: how many beat images came from OpenArt vs the
    # post's hero image. Images are resolved per beat, so any mix is an
    # ordinary success -- OpenArt is an opt-in upgrade, never an error.
    used_openart: bool | None = None
    ai_images: int | None = None
    hero_images: int | None = None


def _image_counts(message: str) -> tuple[int | None, int | None]:
    """AI vs hero image counts from the pipeline's own summary line, as
    captured into `worker_runs.message`. `(None, None)` when no reel was
    composed (nothing to report)."""
    for line in reversed(message.splitlines()):
        if not line.startswith("summary: "):
            continue
        try:
            counts = json.loads(line[len("summary: ") :])
        except ValueError:
            return None, None
        if not isinstance(counts, dict):
            return None, None
        ai = counts.get("ai_images")
        hero = counts.get("hero_images")
        if isinstance(ai, int) and isinstance(hero, int):
            return ai, hero
        return None, None
    return None, None


def _label(brand_id: str) -> str:
    """`worker_runs.worker_label` for this flow.

    Prefixed with the brand so `task_worker._write_flow_log` strips it back to
    `reels-compose` and writes `logs/cron_reels_compose.log`, matching the
    convention the Explorer page's log viewer already expects.
    """
    return f"{brand_id}-{_FLOW_ID}"


@router.post("/reels/compose", status_code=202)
def compose_reels() -> dict[str, str]:
    """Dispatch one pipeline run to the worker. 202 immediately; poll
    /reels/compose/status.

    Deliberately no OpenArt pre-flight. OpenArt is an optional upgrade, not a
    prerequisite: an unconfigured or unauthorized brand still composes reels
    from the post's hero image (see `scripts.reels_images`), so refusing the
    run here would block a feature that works fine without it.
    """
    brand_id, brand_dir = resolve_api_brand()
    label = _label(brand_id)

    with _dispatch_lock:
        current = worker_db.get_one(brand_dir, label, brand_id)
        if current is not None and str(current.get("status")) in _IN_FLIGHT:
            raise HTTPException(status_code=409, detail="a compose run is already in progress")

        # Recorded BEFORE the push so the status endpoint reports the run the
        # moment this returns, rather than a gap where it looks idle until the
        # worker's own record_start lands.
        worker_db.record_start(brand_dir, label, brand_id)
        flow_queue.dispatch(
            schedule_task_id=label,
            script=_PIPELINE_SCRIPT,
            brand=brand_id,
            brand_dir=brand_dir,
            timeout_seconds=_COMPOSE_TIMEOUT_SECONDS,
        )

    log.info(json.dumps({"event": "reels_compose_dispatched", "brand": brand_id, "label": label}))
    return {"status": "started"}


@router.get("/reels/compose/status", response_model=ComposeStatus)
def compose_status() -> ComposeStatus:
    """State of the current/last run, for the frontend's polling loop.

    Read from the shared `worker_runs` row, so it reflects a run executing in
    the worker container (and survives an API restart mid-run).
    """
    brand_id, brand_dir = resolve_api_brand()
    row = worker_db.get_one(brand_dir, _label(brand_id), brand_id)
    if row is None:
        return ComposeStatus(running=False)

    status = str(row.get("status") or "")
    last_run = row.get("last_run")
    last_run_str = str(last_run) if last_run else None
    if status in _IN_FLIGHT:
        # Queued counts as in-progress: the user clicked, work is coming.
        return ComposeStatus(running=True, started_at=last_run_str)

    message = str(row.get("message") or "")
    ai_images, hero_images = _image_counts(message)
    return ComposeStatus(
        running=False,
        finished_at=last_run_str,
        ok=status == "success",
        detail=message[-500:],
        used_openart=None if ai_images is None else ai_images > 0,
        ai_images=ai_images,
        hero_images=hero_images,
    )
