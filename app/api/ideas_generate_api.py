"""Trigger the CrewAI content scout (Trends + Idea crews) from the frontend.

  POST /api/v1/ideas/generate         — start a scout run in the background
  GET  /api/v1/ideas/generate/status  — is one running / how did the last end

A separate router from `api.ideas_api` for the same reason `social_posts_api`
is: that module is past the file-size limit and its PATCH handler is already
overloaded. This one owns exactly one concern — running
`scripts/crewai_content_scout.py --apply` on demand instead of only from a
terminal.

Concurrency: one run at a time, enforced with a non-blocking module-level
lock. The scout is expensive (Serper searches + two DeepSeek crews, minutes
of wall-clock) and self-deduplicating against existing topics — but two
concurrent runs would race that dedup check and write duplicate ideas, so a
second click while one is in flight gets 409 rather than a second crew.
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
_SCOUT_TIMEOUT_SECONDS = 900  # Serper + two crews; generous, not unbounded

log = logging.getLogger(__name__)

router = APIRouter(tags=["ideas"])

_run_lock = threading.Lock()
# Written only while _run_lock is held (or before any run exists) -- the
# status endpoint reads it lock-free, which is safe for a dict swapped
# atomically by reference.
_last_run: dict[str, Any] = {}


class GenerateStatus(BaseModel):
    running: bool
    started_at: str | None = None
    finished_at: str | None = None
    ok: bool | None = None
    detail: str | None = None


def _run_scout() -> None:
    """The background task: run the scout subprocess and record the outcome.

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
            [sys.executable, "-m", "scripts.crewai_content_scout", "--apply"],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=_SCOUT_TIMEOUT_SECONDS,
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
                    "event": "ideas_generate_finished",
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
            "detail": f"scout timed out after {_SCOUT_TIMEOUT_SECONDS}s",
        }
        log.error(json.dumps({"event": "ideas_generate_timeout"}))
    except Exception as exc:
        _last_run = {
            "running": False,
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "ok": False,
            "detail": str(exc)[-500:],
        }
        log.error(json.dumps({"event": "ideas_generate_error", "error": str(exc)}))
    finally:
        _run_lock.release()


@router.post("/ideas/generate", status_code=202)
def generate_ideas(background_tasks: BackgroundTasks) -> dict[str, str]:
    """Start one scout run. 202 immediately; poll /ideas/generate/status."""
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a generation run is already in progress")
    background_tasks.add_task(_run_scout)
    return {"status": "started"}


@router.get("/ideas/generate/status", response_model=GenerateStatus)
def generate_status() -> GenerateStatus:
    """State of the current/last run, for the frontend's polling loop."""
    if _run_lock.locked() and _last_run.get("running"):
        return GenerateStatus(running=True, started_at=_last_run.get("started_at"))
    if not _last_run:
        return GenerateStatus(running=False)
    return GenerateStatus(
        running=False,
        started_at=_last_run.get("started_at"),
        finished_at=_last_run.get("finished_at"),
        ok=_last_run.get("ok"),
        detail=_last_run.get("detail"),
    )
