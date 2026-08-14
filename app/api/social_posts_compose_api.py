"""Trigger the social-posts composition pass from the frontend.

  POST /api/v1/social-posts/compose         — dispatch a compose run to the worker
  GET  /api/v1/social-posts/compose/status  — is one queued/running / how did it end

Same shape and same reasoning as `api/reels_compose_api.py`: the API image
carries no LLM/image credentials and no font stack, so this module
*dispatches* onto the shared `flow-run` Redis queue that
`scripts/task_dispatcher.py` and `POST /brands/{id}/flows/{flow_id}/run` use,
and `scripts/task_worker.py` executes it with the brand's own environment.

One difference from reels: this flow is ALSO on cron (`social-posts-compose`,
daily), so the button dispatches that flow's own `schedule_tasks` row rather
than a parallel hardcoded one. Sharing the row means sharing its
`worker_runs` label, which buys two things: clicking while the daily run is
in flight is refused (409) instead of racing it, and a manual run counts as
the day's run for `lib.scheduling.is_task_due`. The hardcoded fallback below
only applies to a brand that has no such row provisioned.

Publishing is deliberately NOT reachable from here: the dispatched args are
the row's own `--compose-only`, so the button can only fill the review queue.
`social-posts-release` remains the sole publisher.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.brand_context import resolve_api_brand
from lib import schedule_db, social_post_db, worker_db
from lib.task_queue import TaskQueue

# Crew + Gemini image per candidate, up to `--limit` (5) of them per run --
# matches the flow's own `timeout_minutes: 30` in profiles/_engine.json.
_COMPOSE_TIMEOUT_SECONDS = 1800
_PIPELINE_SCRIPT = "scripts/crewai_social_posts_pipeline.py"
_COMPOSE_ARGS = ["--compose-only"]
# Must match scripts/task_worker.py's QUEUE_WORKER -- same Redis list.
_QUEUE_WORKER = "flow-run"
_FLOW_ID = "social-posts-compose"

_QUEUED = "queued"
_RUNNING = "running"
_IN_FLIGHT = frozenset({_QUEUED, _RUNNING})

log = logging.getLogger(__name__)

router = APIRouter(tags=["social-posts"])

# Makes check-then-enqueue atomic within this process; the shared
# `worker_runs` row is the real cross-process single-flight guard.
_dispatch_lock = threading.Lock()


class ComposeStatus(BaseModel):
    running: bool
    started_at: str | None = None
    finished_at: str | None = None
    ok: bool | None = None
    detail: str | None = None
    # How many posts this run added to the review queue, from the pipeline's
    # own summary line. None when the run composed nothing (no candidates).
    composed: int | None = None
    # Published articles with no social-post track started, i.e. what a run
    # right now would work from. Reported BEFORE the click so a run that can
    # only be a no-op says so up front -- a successful run that composes
    # nothing is otherwise indistinguishable from a button that did nothing.
    candidates: int = 0


def _candidate_count(brand_id: str) -> int:
    """Eligible ideas right now (same query the pipeline composes from)."""
    return len(social_post_db.list_candidates(brand_id=brand_id, limit=500))


def _scheduled_task(brand_id: str) -> dict[str, Any] | None:
    """This brand's `social-posts-compose` row, matched the way
    `api/brand_flows_api.py` matches: on (brand_id, title), because the one
    legacy brand's row ids use a `dogfood-` prefix unrelated to its brand_id.
    """
    return next(
        (
            t
            for t in schedule_db.load_all()
            if t.get("brand_id") == brand_id and t.get("title") == _FLOW_ID
        ),
        None,
    )


def _compose_args(row_args: Any) -> list[str]:
    """The row's args, forced into compose-only mode.

    The button is a "fill my review queue" affordance, so it must not be able
    to publish no matter how the row is configured -- an edited or
    hand-written row carrying `--release-only` (or no mode flag at all, which
    means compose AND release) would otherwise publish straight from a click,
    and could race the hourly release flow while doing it.
    """
    args = [str(a) for a in (row_args or []) if str(a) != "--release-only"]
    return args if "--compose-only" in args else [*args, "--compose-only"]


def _dispatch_plan(brand_id: str) -> tuple[str, list[str], int]:
    """`(worker_label, args, timeout_seconds)` for this brand's compose run.

    Prefers the provisioned row so a manual run is the same run cron makes;
    falls back to this module's constants for a brand that was never
    provisioned, so the button still works rather than 404-ing.
    """
    task = _scheduled_task(brand_id)
    if task is None:
        return f"{brand_id}-{_FLOW_ID}", list(_COMPOSE_ARGS), _COMPOSE_TIMEOUT_SECONDS
    timeout_minutes = task.get("timeout_minutes")
    return (
        str(task["id"]),
        _compose_args(task.get("args")),
        int(timeout_minutes) * 60 if timeout_minutes else _COMPOSE_TIMEOUT_SECONDS,
    )


def _composed_count(message: str) -> int | None:
    """Posts queued by the run, from its `summary: {...}` line as captured
    into `worker_runs.message`. The pipeline keys the count by image source
    (`composed_gemini` / `composed_fallback`), so sum every `composed_*`."""
    for line in reversed(message.splitlines()):
        if not line.startswith("summary: "):
            continue
        try:
            counts = json.loads(line[len("summary: ") :])
        except ValueError:
            return None
        if not isinstance(counts, dict):
            return None
        composed = [
            v for k, v in counts.items() if k.startswith("composed_") and isinstance(v, int)
        ]
        return sum(composed) if composed else 0
    return None


@router.post("/social-posts/compose", status_code=202)
def compose_social_posts() -> dict[str, str]:
    """Dispatch one composition run to the worker. 202 immediately; poll
    /social-posts/compose/status."""
    brand_id, brand_dir = resolve_api_brand()
    label, args, timeout_seconds = _dispatch_plan(brand_id)

    with _dispatch_lock:
        current = worker_db.get_one(brand_dir, label, brand_id)
        if current is not None and str(current.get("status")) in _IN_FLIGHT:
            raise HTTPException(status_code=409, detail="a compose run is already in progress")

        # Recorded BEFORE the push so the status endpoint reports the run the
        # moment this returns, rather than a gap where it looks idle.
        worker_db.record_start(brand_dir, label, brand_id)
        payload: dict[str, Any] = {
            "schedule_task_id": label,
            "script": _PIPELINE_SCRIPT,
            "args": args,
            "brand": brand_id,
            "brand_dir": brand_dir,
            "timeout_seconds": timeout_seconds,
        }
        TaskQueue(worker=_QUEUE_WORKER, brand=brand_id).push(payload)

    log.info(
        json.dumps({"event": "social_posts_compose_dispatched", "brand": brand_id, "label": label})
    )
    return {"status": "started"}


@router.get("/social-posts/compose/status", response_model=ComposeStatus)
def compose_status() -> ComposeStatus:
    """State of the current/last run (cron's or the button's -- same row)."""
    brand_id, brand_dir = resolve_api_brand()
    label, _args, _timeout = _dispatch_plan(brand_id)
    candidates = _candidate_count(brand_id)
    row = worker_db.get_one(brand_dir, label, brand_id)
    if row is None:
        return ComposeStatus(running=False, candidates=candidates)

    status = str(row.get("status") or "")
    last_run = row.get("last_run")
    last_run_str = str(last_run) if last_run else None
    if status in _IN_FLIGHT:
        # Queued counts as in-progress: the user clicked, work is coming.
        return ComposeStatus(running=True, started_at=last_run_str, candidates=candidates)

    message = str(row.get("message") or "")
    return ComposeStatus(
        running=False,
        finished_at=last_run_str,
        ok=status == "success",
        detail=message[-500:],
        composed=_composed_count(message),
        candidates=candidates,
    )
