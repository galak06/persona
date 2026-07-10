from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from lib.tiktok_scout.state import load_candidates, update_status

router = APIRouter(tags=["tiktok"])


class TikTokCandidateItem(BaseModel):
    handle: str
    display_name: str
    bio: str
    follower_count: int
    source_hashtag: str
    discovered_at: str
    status: str


class CandidatesResponse(BaseModel):
    candidates: list[TikTokCandidateItem]
    total: int
    counts: dict[str, int]


class StatusUpdate(BaseModel):
    status: Literal["pending", "followed", "skipped"]


@router.get("/tiktok-candidates", response_model=CandidatesResponse)
def list_candidates(
    status: str | None = Query(None, description="Filter by status: pending, followed, skipped"),
) -> CandidatesResponse:
    """List TikTok follow candidates."""
    all_rows = load_candidates()
    counts: dict[str, int] = {}
    for r in all_rows:
        s = str(r.get("status", "pending"))
        counts[s] = counts.get(s, 0) + 1
    filtered = [r for r in all_rows if not status or r.get("status") == status]
    candidates = [TikTokCandidateItem(**r) for r in filtered]  # type: ignore[arg-type]
    return CandidatesResponse(candidates=candidates, total=len(candidates), counts=counts)


@router.patch("/tiktok-candidates/{handle}/status")
def update_candidate_status(handle: str, body: StatusUpdate) -> dict:
    """Update the status of a TikTok candidate."""
    rows = load_candidates()
    if not any(str(r.get("handle", "")).lower() == handle.lower() for r in rows):
        raise HTTPException(status_code=404, detail=f"Candidate '{handle}' not found")
    update_status(handle, body.status)
    return {"handle": handle, "status": body.status}
