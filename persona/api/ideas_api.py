"""Content Ideas API — CRUD for the content_ideas Supabase table.

Exposes:
  GET  /api/v1/ideas             — list ideas (filter by category / status)
  PATCH /api/v1/ideas/{id}/status — transition idea status
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from lib import ideas_db

_PUBLISHER_DIR = Path(__file__).resolve().parents[1] / "recipe-publisher"
if str(_PUBLISHER_DIR) not in sys.path:
    sys.path.insert(0, str(_PUBLISHER_DIR))

try:
    from publishers.wordpress_ideas import publish_idea_to_wordpress
    _WP_PUBLISH_AVAILABLE = True
except ImportError:
    _WP_PUBLISH_AVAILABLE = False

log = logging.getLogger(__name__)


def _slides_dir(idea_id: str) -> Path:
    brand = os.environ.get("BRAND_DIR")
    base = Path(brand) if brand else Path(__file__).resolve().parents[1]
    return base / "data" / "media" / "content_carousels" / idea_id


def _load_enrichment_cache(brand_dir: str) -> dict[str, dict]:
    path = Path(brand_dir) / "state" / "enrichment_cache.json"
    if not path.exists():
        return {}
    try:
        entries = json.loads(path.read_text())
        return {e["topic"].lower(): e for e in entries if "topic" in e}
    except Exception:
        return {}


def _publish_idea_background(idea_id: str) -> None:
    if not _WP_PUBLISH_AVAILABLE:
        log.warning('"wordpress_ideas publisher not available, skipping background publish"')
        return
    try:
        rows = ideas_db.list_ideas(status="approved", brand_id=None, limit=50)
        idea = next((r for r in rows if r["id"] == idea_id), None)
        if not idea:
            log.warning(f'"idea {idea_id} not found in approved status (may have been claimed)"')
            return
        brand_dir = os.getenv("BRAND_DIR", "")
        enrichment_cache = _load_enrichment_cache(brand_dir)
        enrichment = enrichment_cache.get(idea["topic"].lower())
        ideas_db.update_status(idea_id, "wp_draft")
        result = publish_idea_to_wordpress(idea, enrichment)
        ideas_db.set_wp_result(idea_id, result.post_id, result.permalink)
        ideas_db.update_status(idea_id, "wp_published")
        log.info(json.dumps({"event": "idea_published", "idea_id": idea_id, "wp_url": result.permalink}))
    except Exception as exc:
        log.error(json.dumps({"event": "idea_publish_failed", "idea_id": idea_id, "error": str(exc)}))
        try:
            ideas_db.update_status(idea_id, "approved")
        except Exception:
            pass


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


class IdeasResponse(BaseModel):
    ideas: list[ContentIdea]
    total: int
    counts: dict[str, int]


class StatusBody(BaseModel):
    status: str


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
            created_at=r.get("created_at"),
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


@router.patch("/ideas/{idea_id}/status")
def update_idea_status(
    idea_id: str,
    body: StatusBody,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Update the status of a single idea."""
    if body.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{body.status}'. Valid: {sorted(VALID_STATUSES)}",
        )
    ok = ideas_db.update_status(idea_id, body.status)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update idea status")
    if body.status == "approved":
        background_tasks.add_task(_publish_idea_background, idea_id)
    return {"id": idea_id, "status": body.status}
