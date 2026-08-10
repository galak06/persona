"""Social-post review API — the FB+IG post track on ``content_ideas``.

A separate router from ``api.ideas_api`` on purpose: that module (and its
``PATCH /ideas/{id}/status`` handler in particular) is already carrying the
drafting AND reel-review transitions on one overloaded endpoint, and is past
the file-size limit. This track gets its own resource-shaped routes instead:

  GET   /api/v1/social-posts                     — queued-for-review list
  GET   /api/v1/social-posts/{id}/image          — the composed hook image
  POST  /api/v1/social-posts/{id}/approve        — publish FB now, arm IG
  POST  /api/v1/social-posts/{id}/reject         — terminal reject + cleanup

Approve publishes ONLY the Facebook half (subprocess →
``worker_wp_ideas_social_post --platform fb``). The IG half is armed with a
due-time and released later by ``scripts/crewai_social_posts_pipeline.py``'s
release sweep — the FB<->IG publishing gap is the point, see
``lib.social_post_db``.
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

from lib import ideas_db, social_post_db

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

log = logging.getLogger(__name__)

router = APIRouter(tags=["social-posts"])


def _resolve_brand_path(relative_path: str) -> Path:
    """Same contract as ``api.ideas_api._resolve_brand_path``: DB paths are
    stored relative to ``BRAND_DIR`` so composer (host) and API (container)
    agree despite different absolute mounts."""
    brand = os.environ.get("BRAND_DIR")
    base = Path(brand) if brand else _PROJECT_ROOT
    return base / relative_path


class SocialPost(BaseModel):
    id: str
    topic: str
    wp_url: str | None = None
    social_post_status: str
    fb_caption: str | None = None
    ig_caption: str | None = None
    image_alt: str | None = None
    source: str | None = None
    validation_flags: list[str] | None = None
    fb_page_post_url: str | None = None
    ig_post_url: str | None = None
    ig_due_at: str | None = None


class SocialPostsResponse(BaseModel):
    posts: list[SocialPost]
    total: int


def _to_model(r: dict[str, Any]) -> SocialPost:
    return SocialPost(
        id=str(r["id"]),
        topic=r.get("topic") or "",
        wp_url=r.get("wp_url"),
        social_post_status=r.get("social_post_status") or "",
        fb_caption=r.get("social_post_fb_caption"),
        ig_caption=r.get("social_post_ig_caption"),
        image_alt=r.get("social_post_image_alt"),
        source=r.get("social_post_source"),
        validation_flags=r.get("social_post_validation_flags"),
        fb_page_post_url=r.get("fb_page_post_url"),
        ig_post_url=r.get("ig_post_url"),
        ig_due_at=str(r["social_post_ig_due_at"]) if r.get("social_post_ig_due_at") else None,
    )


@router.get("/social-posts", response_model=SocialPostsResponse)
def list_social_posts(
    status: str = Query("queued", description="social_post_status filter"),
    brand_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> SocialPostsResponse:
    """Rows on the social-post track, `'queued'` (awaiting review) by default."""
    if status not in social_post_db.STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{status}'. Valid: {sorted(social_post_db.STATUSES)}",
        )
    rows = [
        r
        for r in ideas_db.list_ideas(brand_id=brand_id, limit=1000)
        if r.get("social_post_status") == status
    ][:limit]
    posts = [_to_model(r) for r in rows]
    return SocialPostsResponse(posts=posts, total=len(posts))


@router.get("/social-posts/{idea_id}/image")
def get_social_post_image(idea_id: str) -> FileResponse:
    """Serve the composed hook image for the review page."""
    idea = ideas_db.get_idea(idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="idea not found")
    path_str = idea.get("social_post_image_path")
    if not path_str:
        raise HTTPException(status_code=404, detail="social post image not composed yet")
    target = _resolve_brand_path(path_str)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="social post image missing on disk")
    return FileResponse(target, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _publish_fb_background(idea_id: str) -> None:
    """Subprocess → the publish worker, FB platform only. The worker owns the
    DB transition (``set_fb_result``) and the rate-limit check; a failure
    leaves the row at 'queued' so Approve can simply be clicked again."""
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "workers.worker_wp_ideas_social_post",
                "--idea-id",
                idea_id,
                "--platform",
                "fb",
            ],
            cwd=_PROJECT_ROOT / "recipe-publisher",
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        log.error(json.dumps({"event": "social_post_fb_publish_timeout", "idea_id": idea_id}))
        return
    except Exception as exc:
        log.error(
            json.dumps(
                {
                    "event": "social_post_fb_publish_subprocess_error",
                    "idea_id": idea_id,
                    "error": str(exc),
                }
            )
        )
        return

    if result.returncode == 0:
        log.info(json.dumps({"event": "social_post_fb_publish_dispatched_ok", "idea_id": idea_id}))
    else:
        log.error(
            json.dumps(
                {
                    "event": "social_post_fb_publish_dispatch_failed",
                    "idea_id": idea_id,
                    "returncode": result.returncode,
                    "stderr_tail": (result.stderr or "")[-2000:],
                }
            )
        )


@router.post("/social-posts/{idea_id}/approve")
def approve_social_post(idea_id: str, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Approve a queued social post: publish FB now (background), arm IG.

    Returns immediately with the current status -- the real flip to
    'fb_published' happens in the worker once the FB post is actually live.
    """
    idea = ideas_db.get_idea(idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="idea not found")
    if idea.get("social_post_status") != "queued":
        raise HTTPException(
            status_code=409,
            detail=f"idea is '{idea.get('social_post_status')}', not 'queued'",
        )
    background_tasks.add_task(_publish_fb_background, idea_id)
    return {"id": idea_id, "status": "queued"}


@router.post("/social-posts/{idea_id}/reject")
def reject_social_post(idea_id: str) -> dict[str, str]:
    """Reject a queued social post: terminal 'rejected' + delete the image.

    Terminal on purpose — see ``lib.social_post_db.reject``: a NULL reset
    would put the row straight back into the composition pipeline's candidate
    query and it would be regenerated (LLM + image spend) on every run.
    """
    idea = ideas_db.get_idea(idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="idea not found")
    if not social_post_db.reject(idea_id):
        raise HTTPException(status_code=409, detail="idea is not 'queued'")
    path_str = idea.get("social_post_image_path")
    if path_str:
        target = _resolve_brand_path(path_str)
        if target.exists():
            target.unlink()
    return {"id": idea_id, "status": "rejected"}
