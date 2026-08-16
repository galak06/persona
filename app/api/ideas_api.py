"""Content Ideas API — CRUD for the content_ideas Postgres table.

Exposes:
  GET  /api/v1/ideas                        — list ideas (filter by category / status)
  GET  /api/v1/ideas/{id}/slides             — recipe carousel slide URLs
  GET  /api/v1/ideas/{id}/slides/{n}         — one carousel slide JPEG
  GET  /api/v1/ideas/{id}/reel-video/{platform} — composed reel mp4 ('ig'/'fb')
  PATCH /api/v1/ideas/{id}/status            — transition idea status (also
                                                drives reel Approve/Reject —
                                                see update_idea_status)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from lib import ideas_db

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

log = logging.getLogger(__name__)


def _slides_dir(idea_id: str) -> Path:
    brand = os.environ.get("BRAND_DIR")
    base = Path(brand) if brand else Path(__file__).resolve().parents[1]
    return base / "data" / "media" / "content_carousels" / idea_id


def _resolve_brand_path(relative_path: str) -> Path:
    """Resolve a `content_ideas.reel_*_video_path` value (stored relative to
    `BRAND_DIR` -- see `worker_wp_ideas_reel.py`) against *this process's
    own* `BRAND_DIR`. Same reasoning as `_slides_dir`: the composer worker
    and this API server can run in different environments (host vs.
    container) with the same `brands/` tree mounted at different absolute
    paths, so a path stored relative to `BRAND_DIR` is the only shape that
    resolves correctly in both."""
    brand = os.environ.get("BRAND_DIR")
    base = Path(brand) if brand else Path(__file__).resolve().parents[1]
    return base / relative_path


def _revert_stale_claim(idea_id: str) -> None:
    """If the idea is still sitting at "drafting" (the subprocess never
    reached a point where it set its own terminal status -- write_failed,
    validation_failed, or wp_draft via create_wp_draft), revert it back to
    "approved" so it's a valid retry candidate for the next
    worker_wp_ideas.py sweep. Any other status means the subprocess already
    classified its own outcome, so it's left untouched."""
    idea = ideas_db.get_idea(idea_id)
    if idea is not None and idea.get("status") == "drafting":
        ideas_db.update_status(idea_id, "approved")


def _draft_idea_with_crewai_background(idea_id: str) -> None:
    """Runs the CrewAI strategist->writer->quality-editor pipeline for one
    approved idea via subprocess, producing a WordPress DRAFT (never
    publishes live). create_wp_draft (inside the subprocess) already
    updates content_ideas' status/wp_result on success; on failure the
    subprocess itself sets write_failed/validation_failed where it can
    identify the cause. This wrapper atomically claims the idea via
    claim_idea_for_drafting (the sole concurrency guard against a parallel
    worker_wp_ideas.py sweep claiming the same idea), logs the outcome, and
    -- only when the subprocess never reached one of its own terminal
    statuses -- reverts the claim back to "approved" for retry."""
    if not ideas_db.claim_idea_for_drafting(idea_id):
        log.warning(json.dumps({"event": "idea_draft_skipped_not_approved", "idea_id": idea_id}))
        return
    try:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.crewai_content_pipeline", "--idea-id", idea_id],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        log.error(json.dumps({"event": "idea_draft_timeout", "idea_id": idea_id}))
        _revert_stale_claim(idea_id)
        return
    except Exception as exc:
        log.error(
            json.dumps({"event": "idea_draft_subprocess_error", "idea_id": idea_id, "error": str(exc)})
        )
        _revert_stale_claim(idea_id)
        return

    if result.returncode == 0:
        log.info(json.dumps({"event": "idea_drafted", "idea_id": idea_id}))
    else:
        stdout_tail = (result.stdout or "")[-2000:]
        stderr_tail = (result.stderr or "")[-2000:]
        # The 2000-char tail alone is not enough to diagnose a writer failure.
        # `lib/crew/writer/execute.py` deliberately logs the model's FULL raw
        # output (its comment records that a short excerpt was "proven useless
        # for real diagnosis"), but that line is emitted early in a run that
        # ends with tens of KB of catalog keys -- so the tail reliably cuts off
        # both the event name and the head of the JSON, exactly the parts that
        # say what broke. Live: three `write_failed` ideas on 2026-08-15 whose
        # cause could not be recovered from these logs at all.
        # So: keep the tail inline for quick reads, and spill the whole thing
        # next to the draft that was being written. Best-effort -- a failure to
        # write the diagnostic must never mask the draft failure itself.
        log_path = _resolve_brand_path(f"state/crew_drafts/{idea_id}.failure.log")
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                f"returncode={result.returncode}\n\n"
                f"=== stdout ===\n{result.stdout or ''}\n\n"
                f"=== stderr ===\n{result.stderr or ''}\n",
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning(
                json.dumps(
                    {"event": "idea_draft_log_write_failed", "idea_id": idea_id, "error": str(exc)}
                )
            )
        log.error(
            json.dumps(
                {
                    "event": "idea_draft_failed",
                    "idea_id": idea_id,
                    "returncode": result.returncode,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "full_log": str(log_path),
                }
            )
        )
        _revert_stale_claim(idea_id)


def _publish_reel_background(idea_id: str) -> None:
    """Runs the reel publish worker (subprocess) for one approved reel.
    Publishing is per-platform-independent -- see
    `recipe-publisher/workers/worker_wp_ideas_reel_publish.py`'s docstring.
    A partial failure just leaves the idea at `social_queued`; re-approving
    from `Reels.tsx` re-invokes this, retrying only the platform still
    missing a URL."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "workers.worker_wp_ideas_reel_publish", "--idea-id", idea_id],
            cwd=_PROJECT_ROOT / "recipe-publisher",
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        log.error(json.dumps({"event": "reel_publish_timeout", "idea_id": idea_id}))
        return
    except Exception as exc:
        log.error(
            json.dumps({"event": "reel_publish_subprocess_error", "idea_id": idea_id, "error": str(exc)})
        )
        return

    if result.returncode == 0:
        log.info(json.dumps({"event": "reel_publish_dispatched_ok", "idea_id": idea_id}))
    else:
        stderr_tail = (result.stderr or "")[-2000:]
        log.error(
            json.dumps(
                {
                    "event": "reel_publish_dispatch_failed",
                    "idea_id": idea_id,
                    "returncode": result.returncode,
                    "stderr_tail": stderr_tail,
                }
            )
        )


def _delete_reel_files(idea: dict[str, Any]) -> None:
    """Best-effort delete of both local reel mp4s -- a missing file is not
    an error (already cleaned up, or this reel used the same shared file
    for both platforms in the `source='openart'` case)."""
    for key in ("reel_ig_video_path", "reel_fb_video_path"):
        path_str = idea.get(key)
        if not path_str:
            continue
        path = _resolve_brand_path(path_str)
        if path.exists():
            path.unlink()


router = APIRouter(tags=["ideas"])

VALID_STATUSES = set(ideas_db.STATUSES)
CATEGORIES = [
    "recipes", "health", "training", "nutrition",
    "gear-toys", "grooming", "breed-specific", "safety",
]


class ContentIdea(BaseModel):
    id: str
    category: str
    topic: str
    target_keyword: str | None = None
    nalla_context: str | None = None
    post_goal: str | None = None
    status: str
    input: str | None = None
    brand_id: str | None = None
    brand_name: str | None = None
    created_at: str | None = None
    match_score: float | None = None
    reel_ig_caption: str | None = None
    reel_fb_caption: str | None = None
    reel_source: str | None = None
    reel_validation_flags: list[str] | None = None
    # Only ever non-null for write_failed/validation_failed (ideas_db clears it
    # on any other transition), so the UI can key off it directly.
    failure_reason: str | None = None


class IdeasResponse(BaseModel):
    ideas: list[ContentIdea]
    total: int
    counts: dict[str, int]


class StatusBody(BaseModel):
    status: str


def _extract_match_score(input_json: str | None) -> float | None:
    """Best-effort extraction of a 0-100 match/priority score from the
    idea's raw `input` JSON blob. Different idea sources use different key
    names (`lib.crew.scout`'s CrewAI path writes "priority_score",
    `lib.gsc_scout`'s older path writes "score"), and some rows predate
    either convention entirely (e.g. the content-ideator skill's manual
    `Input: "1"` placeholder) -- never raises, returns None on anything
    that isn't a parseable JSON object with one of those keys."""
    if not input_json:
        return None
    try:
        data = json.loads(input_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    score = data.get("priority_score", data.get("score"))
    if score is None:
        return None
    try:
        return float(score)
    except (TypeError, ValueError):
        return None


@router.get("/ideas", response_model=IdeasResponse)
def list_ideas(
    category: str | None = Query(None, description="Filter by category"),
    status: str | None = Query(None, description="Filter by status (default: all)"),
    brand_id: str | None = Query(None, description="Filter by brand"),
    limit: int = Query(200, ge=1, le=1000),
) -> IdeasResponse:
    """List content ideas, newest first. Defaults to all statuses."""
    rows = ideas_db.list_ideas(status=status, brand_id=brand_id, limit=limit)
    if category:
        rows = [r for r in rows if r.get("category") == category]

    ideas = [
        ContentIdea(
            id=str(r["id"]),
            category=r.get("category") or "",
            topic=r.get("topic") or "",
            target_keyword=r.get("target_keyword"),
            nalla_context=r.get("nalla_context"),
            post_goal=r.get("post_goal"),
            status=r.get("status") or "publish",
            input=r.get("input"),
            brand_id=r.get("brand_id"),
            brand_name=r.get("brand_name"),
            created_at=str(r["created_at"]) if r.get("created_at") else None,
            match_score=_extract_match_score(r.get("input")),
            reel_ig_caption=r.get("reel_ig_caption"),
            reel_fb_caption=r.get("reel_fb_caption"),
            reel_source=r.get("reel_source"),
            reel_validation_flags=r.get("reel_validation_flags"),
            failure_reason=r.get("failure_reason"),
        )
        for r in rows
    ]

    counts: dict[str, int] = {}
    for idea in ideas:
        counts[idea.status] = counts.get(idea.status, 0) + 1

    return IdeasResponse(ideas=ideas, total=len(ideas), counts=counts)


@router.get("/ideas/{idea_id}/slides")
def list_idea_slides(idea_id: str) -> dict:
    """Return slide URLs for an idea's saved carousel (empty list if not generated yet)."""
    folder = _slides_dir(idea_id)
    if not folder.exists():
        return {"idea_id": idea_id, "count": 0, "slides": []}
    paths = sorted(
        folder.glob("slide_*.jpg"),
        key=lambda p: int(p.stem.split("_")[1]),
    )
    return {
        "idea_id": idea_id,
        "count": len(paths),
        "slides": [
            {"n": i + 1, "url": f"/api/v1/ideas/{idea_id}/slides/{i + 1}"}
            for i in range(len(paths))
        ],
    }


@router.get("/ideas/{idea_id}/slides/{n}")
def get_idea_slide(idea_id: str, n: int) -> FileResponse:
    """Serve a single carousel slide JPEG."""
    folder = _slides_dir(idea_id)
    target = (folder / f"slide_{n}.jpg").resolve()
    if not target.is_relative_to(folder.resolve()):
        raise HTTPException(status_code=400, detail="invalid slide path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"slide {n} not found")
    return FileResponse(
        target,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/ideas/{idea_id}/reel-video/{platform}")
def get_reel_video(idea_id: str, platform: str) -> FileResponse:
    """Serve a composed reel mp4 for the `Reels.tsx` review page's preview
    player. `platform` is `ig` or `fb` -- always two distinct files (each
    platform gets its own safe-zone-tuned overlay render), regardless of
    whether `reel_source` is `'openart'` (5 distinct per-beat AI images) or
    `'fallback'` (the WP post's hero image reused for all 5 beats)."""
    if platform not in ("ig", "fb"):
        raise HTTPException(status_code=400, detail="platform must be 'ig' or 'fb'")
    idea = ideas_db.get_idea(idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="idea not found")
    column = "reel_ig_video_path" if platform == "ig" else "reel_fb_video_path"
    path_str = idea.get(column)
    if not path_str:
        raise HTTPException(status_code=404, detail="reel video not composed yet")
    target = _resolve_brand_path(path_str)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="reel video file missing on disk")
    return FileResponse(
        target,
        media_type="video/mp4",
        headers={"Cache-Control": "no-store"},
    )


@router.patch("/ideas/{idea_id}/status")
def update_idea_status(
    idea_id: str,
    body: StatusBody,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Update the status of a single idea.

    Reel-review transitions (Approve/Reject on `Reels.tsx`) are special-
    cased rather than going through a blind status write: Approve
    (`social_queued` -> `social_done`) needs to trigger the actual publish
    subprocess, not just flip a column -- the row only really reaches
    `social_done` once `worker_wp_ideas_reel_publish.py`/`set_reel_result`
    confirms both platforms are live. Reject (`social_queued` ->
    `wp_published`) needs to clear the pending-review columns and delete
    the local mp4(s) too, not just flip status -- `ideas_db.reject_reel`
    handles the DB half, this handler does the file cleanup.
    """
    if body.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{body.status}'. Valid: {sorted(VALID_STATUSES)}",
        )

    current = ideas_db.get_idea(idea_id)
    if current is None:
        raise HTTPException(status_code=404, detail="idea not found")

    if body.status == "social_done" and current.get("status") == "social_queued":
        background_tasks.add_task(_publish_reel_background, idea_id)
        return {"id": idea_id, "status": "social_queued"}  # publish runs in the background

    if body.status == "wp_published" and current.get("status") == "social_queued":
        ok = ideas_db.reject_reel(idea_id)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to reject reel")
        _delete_reel_files(current)
        return {"id": idea_id, "status": "wp_published"}

    ok = ideas_db.update_status(idea_id, body.status)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update idea status")
    if body.status == "approved":
        background_tasks.add_task(_draft_idea_with_crewai_background, idea_id)
    return {"id": idea_id, "status": body.status}
