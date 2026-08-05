"""Read-only API for the published posts + comments history (engagements.db).

Surfaces rows recorded by the publish workers (``fb_comment.py``,
``fb_group_post.py``, ``publish_prepared.py``) so the UI can show what we've
posted and commented, across platforms. Strictly read-only.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from lib import engagements_db

router = APIRouter()


class Engagement(BaseModel):
    """One published post or comment."""

    id: str
    platform: str
    kind: str
    status: str
    target_name: str = ""
    target_url: str = ""
    permalink: str = ""
    content: str = ""
    source_ref: str = ""
    error: str = ""
    posted_at: str = ""


class EngagementsResponse(BaseModel):
    engagements: list[Engagement]
    total: int
    counts: dict[str, int]


@router.get("/engagements", response_model=EngagementsResponse)
def list_engagements_endpoint(
    platform: str | None = Query(None, description="facebook | instagram | wordpress"),
    kind: str | None = Query(None, description="comment | link_post | feed_post | reel | page_post"),
    status: str | None = Query(None, description="posted | failed"),
    limit: int = Query(200, ge=1, le=1000),
) -> EngagementsResponse:
    """Most-recent-first published posts/comments, optionally filtered, plus counts."""
    rows = engagements_db.list_engagements(
        platform=platform, kind=kind, status=status, limit=limit
    )
    items = [
        Engagement(**{k: (r.get(k) or "") for k in Engagement.model_fields})
        for r in rows
    ]
    return EngagementsResponse(
        engagements=items, total=len(items), counts=engagements_db.counts()
    )
