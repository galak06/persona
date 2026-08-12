"""First-comment approval for Facebook groups (fb-engager gate).

The fb-engager flow never posts a group's first-ever comment inline: it
flags the group once (``fb_groups.first_comment_flagged_at``, write-once)
and notifies via Telegram. The endpoint below is the user's one-time
approval — after it, comments in that group post inline forever.

Registered under ``/api/v1`` by ``api.approval_api`` (one include_router
line), alongside the existing GET/PUT ``/facebook/groups`` routes that
still live there.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from api.schemas import FacebookGroup
from lib import groups_db

router = APIRouter()


@router.post(
    "/facebook/groups/{group_name}/approve-first-comment",
    response_model=FacebookGroup,
)
def approve_first_comment(group_name: str) -> FacebookGroup:
    """Approve inline commenting for one group; returns the refreshed group.

    Approving a group that was never flagged is allowed — pre-approval is
    harmless (the group's first comment simply posts inline when it comes).
    """
    group = groups_db.get_by_name(group_name)
    if group is None:
        raise HTTPException(status_code=404, detail=f"group {group_name} not found")

    url = str(group["group_url"])
    groups_db.set_first_comment_approved(url, datetime.now(UTC).isoformat())

    updated = groups_db.get_by_name(group_name)
    return FacebookGroup.model_validate(updated)
