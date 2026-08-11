"""Trigger the CrewAI reels pipeline from the frontend.

  POST /api/v1/reels/compose         — start a compose run in the background
  GET  /api/v1/reels/compose/status  — is one running / how did the last end

Structural twin of `api.ideas_generate_api`, for the same reason: the Reels
page could only *review* reels; composing them meant a terminal. This module
owns exactly one concern — running `scripts/crewai_reels_pipeline.py` on
demand so the 🎬 button exists.

Concurrency: one run at a time via a non-blocking module-level lock. The
pipeline claims ideas row-by-row (`composing_reel` guards concurrent runs at
the DB layer too), but two runs would still race the OpenArt/ffmpeg work and
double the spend — a second click while one is in flight gets 409.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Per idea: crew + image generation + two ffmpeg renders. A five-idea batch
# runs well past the scout's 900s, so this ceiling is wider.
_COMPOSE_TIMEOUT_SECONDS = 1800

log = logging.getLogger(__name__)

router = APIRouter(tags=["reels"])

_run_lock = threading.Lock()
# Written only while _run_lock is held (or before any run exists) -- the
# status endpoint reads it lock-free, which is safe for a dict swapped
# atomically by reference.
_last_run: dict[str, Any] = {}


class ComposeStatus(BaseModel):
    running: bool
    started_at: str | None = None
    finished_at: str | None = None
    ok: bool | None = None
    detail: str | None = None


def _run_pipeline() -> None:
    """The background task: run the pipeline subprocess and record the outcome.

    The lock is acquired by the ROUTE (so the 409 answer is immediate) and
    released here when the run finishes -- acquire/release straddle the
    background boundary on purpose.
    """
    global _last_run
    started = datetime.now(UTC).isoformat()
    outcome: dict[str, Any] = {"running": True, "started_at": started}
    _last_run = outcome
    try:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.crewai_reels_pipeline"],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=_COMPOSE_TIMEOUT_SECONDS,
        )
        tail = (result.stdout or "").strip().splitlines()[-3:]
        _last_run = {
            "running": False,
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "ok": result.returncode == 0,
            "detail": " | ".join(tail) if result.returncode == 0 else (result.stderr or "")[-500:],
        }
        log.info(
            json.dumps(
                {
                    "event": "reels_compose_finished",
                    "returncode": result.returncode,
                    "stdout_tail": tail,
                }
            )
        )
    except subprocess.TimeoutExpired:
        _last_run = {
            "running": False,
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "ok": False,
            "detail": f"compose timed out after {_COMPOSE_TIMEOUT_SECONDS}s",
        }
        log.error(json.dumps({"event": "reels_compose_timeout"}))
    except Exception as exc:
        _last_run = {
            "running": False,
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "ok": False,
            "detail": str(exc)[-500:],
        }
        log.error(json.dumps({"event": "reels_compose_error", "error": str(exc)}))
    finally:
        _run_lock.release()


@router.post("/reels/compose", status_code=202)
def compose_reels(background_tasks: BackgroundTasks) -> dict[str, str]:
    """Start one pipeline run. 202 immediately; poll /reels/compose/status."""
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a compose run is already in progress")
    background_tasks.add_task(_run_pipeline)
    return {"status": "started"}


@router.get("/reels/compose/status", response_model=ComposeStatus)
def compose_status() -> ComposeStatus:
    """State of the current/last run, for the frontend's polling loop."""
    if _run_lock.locked() and _last_run.get("running"):
        return ComposeStatus(running=True, started_at=_last_run.get("started_at"))
    if not _last_run:
        return ComposeStatus(running=False)
    return ComposeStatus(
        running=False,
        started_at=_last_run.get("started_at"),
        finished_at=_last_run.get("finished_at"),
        ok=_last_run.get("ok"),
        detail=_last_run.get("detail"),
    )
