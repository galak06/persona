# pyright: reportMissingImports=false
"""FastAPI approval sidecar.

Mirrors the Telegram approval queues over localhost HTTP so a web UI can
decide in parallel. Surfaces:

- ``GET  /api/v1/pending``       → blog-post pairs + group-join candidates
- ``GET  /api/v1/activity``      → tail of engagement_log.jsonl (read-only)
- ``GET  /api/v1/items/{id}``    → single-item lookup
- ``POST /api/v1/items/{id}/approve`` → dispatches by item ``type``
- ``POST /api/v1/items/{id}/reject``  → dispatches by item ``type``
- ``POST /api/v1/items/{id}/edit``    → blog_post only

Engagement comments no longer surface to the web UI — they flow
autonomously through the scanner → inline Gemini draft → comment_poster
pipeline and are reported via ``/activity``. Items still sitting in
``comment_queue.json`` from before that cut-over are visible to
``get_item`` but every approve/reject/edit returns 410 Gone.

The route handlers below are thin — dispatch lives in
``api.routes_helpers`` to keep this module under the 300-line cap.

Run locally: ``cd social-automation && python -m api.approval_api``.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Literal

from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Query,
    Response,
    status,
)
from fastapi.middleware.cors import CORSMiddleware

from api import state
from api.schemas import (
    ActivityEntry,
    ActivityResponse,
    ApproveBody,
    BlogPostItem,
    DecisionResponse,
    EditBody,
    GroupItem,
    PendingResponse,
    RejectBody,
    FacebookGroup,
    FacebookGroupsResponse,
    FacebookGroupUpdateBody,
)

_REPO_ROOT: Path = Path(__file__).resolve().parent.parent

_log = logging.getLogger("approval_api")
if not _log.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _log.addHandler(_handler)
    _log.setLevel(logging.INFO)


def _load_secrets() -> None:
    """Best-effort import of ``lib.local_env`` to merge settings.local.json
    into ``os.environ``. We do this at module import so uvicorn workers see
    secrets, and tolerate a missing module so the API still boots in CI."""
    try:
        sys.path.insert(0, str(_REPO_ROOT))
        from lib.local_env import load_local_env
    except ImportError:
        _log.warning("local_env not importable; secrets not auto-loaded")
        return
    loaded = load_local_env()
    _log.info("local_env loaded: %d secrets", loaded)


_load_secrets()

# Import the new lib + helper modules *after* secrets load so any
# module-level config-driven paths resolve correctly.
from api import routes_helpers as rh
from lib import activity_log
from lib.config import settings

app = FastAPI(
    title=f"{settings.site.name} Approval API",
    version="0.2.0",
    description="Localhost-only sidecar for parallel web/Telegram approvals.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/api/v1/pending", response_model=PendingResponse)
def list_pending() -> PendingResponse:
    """All blog-post pairs + group-join candidates awaiting a decision."""
    blog_posts_raw = rh.pending_only(state.read_queue(rh.BLOG_POST_QUEUE_PATH))
    from lib import groups_queue
    items: list[BlogPostItem | GroupItem] = []
    for raw in blog_posts_raw:
        try:
            items.append(rh.to_blog_post(raw))
        except (ValueError, TypeError) as exc:
            _log.warning("skipping malformed blog_post %s: %s", raw.get("id"), exc)
    items.extend(groups_queue.read_pending_groups())
    counts = {
        "blog_posts": sum(1 for i in items if isinstance(i, BlogPostItem)),
        "groups_to_join": sum(1 for i in items if isinstance(i, GroupItem)),
        "total": len(items),
    }
    return PendingResponse(items=items, counts=counts, as_of=rh.now_iso())


@app.get("/api/v1/activity", response_model=ActivityResponse)
def list_activity(
    limit: int = Query(default=50, ge=1, le=500),
    platform: Literal["facebook", "instagram", "wordpress"] | None = Query(default=None),
    action: str | None = Query(default=None),
) -> ActivityResponse:
    """Tail of ``logs/engagement_log.jsonl``, most recent first."""
    raw_entries, total = activity_log.read_recent(
        limit=limit, platform=platform, action=action,
    )
    entries: list[ActivityEntry] = []
    for raw in raw_entries:
        try:
            entries.append(ActivityEntry.model_validate(raw))
        except (ValueError, TypeError) as exc:
            _log.warning("skipping malformed activity row: %s", exc)
    return ActivityResponse(entries=entries, total=total, as_of=rh.now_iso())


@app.get("/api/v1/items/{item_id}")
def get_item(item_id: str) -> BlogPostItem | GroupItem:
    """Look up a single item by id. 410 for legacy comments."""
    located = rh.queue_for_id(item_id)
    if located is None:
        raise HTTPException(status_code=404, detail=f"item {item_id} not found")
    kind, _path, raw = located
    if kind == "comment":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="engagement comments are no longer managed via the web UI",
        )
    if kind == "blog_post":
        return rh.to_blog_post(raw)
    return GroupItem.model_validate(raw)


@app.post("/api/v1/items/{item_id}/approve", response_model=DecisionResponse)
def approve_item(
    item_id: str,
    background_tasks: BackgroundTasks,
    body: ApproveBody | None = None,
    channel: str | None = Query(default=None, pattern="^(both|fb_only|ig_only)$"),
) -> DecisionResponse:
    """Approve an item. Dispatches on type."""
    located = rh.queue_for_id(item_id)
    if located is None:
        raise HTTPException(status_code=404, detail=f"item {item_id} not found")
    kind, path, raw = located
    if kind == "comment":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="engagement comments are no longer managed via the web UI",
        )
    if kind == "blog_post":
        assert path is not None
        payload = body or ApproveBody()
        return rh.approve_blog_post(
            path, item_id, channel=channel, text=payload.text,
            fb_caption=payload.fb_caption, ig_caption=payload.ig_caption,
            decision_status="approved",
        )
    return rh.approve_group(raw, status_value="approved", background_tasks=background_tasks)


@app.post("/api/v1/items/{item_id}/reject", response_model=DecisionResponse)
def reject_item(
    item_id: str,
    background_tasks: BackgroundTasks,
    body: RejectBody | None = None,
) -> DecisionResponse:
    """Reject an item. Dispatches on type. Logs ``body.reason`` (free-form)."""
    located = rh.queue_for_id(item_id)
    if located is None:
        raise HTTPException(status_code=404, detail=f"item {item_id} not found")
    kind, path, raw = located
    if body and body.reason:
        _log.info("reject reason for %s: %s", item_id, body.reason)
    if kind == "comment":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="engagement comments are no longer managed via the web UI",
        )
    if kind == "blog_post":
        assert path is not None
        return rh.approve_blog_post(
            path, item_id, channel=None, text=None,
            fb_caption=None, ig_caption=None, decision_status="USER_SKIPPED",
        )
    return rh.approve_group(
        raw, status_value="USER_SKIPPED", background_tasks=background_tasks,
    )


@app.post("/api/v1/items/{item_id}/edit", response_model=DecisionResponse)
def edit_item(item_id: str, body: EditBody) -> DecisionResponse:
    """Approve with edited content. Only valid for blog_post items."""
    if body.text is None and body.fb_caption is None and body.ig_caption is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="edit requires at least one of: text, fb_caption, ig_caption",
        )
    located = rh.queue_for_id(item_id)
    if located is None:
        raise HTTPException(status_code=404, detail=f"item {item_id} not found")
    kind, path, _raw = located
    if kind == "comment":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="engagement comments are no longer managed via the web UI",
        )
    if kind == "group_to_join":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="group items have no editable text",
        )
    assert path is not None
    return rh.approve_blog_post(
        path, item_id, channel=None, text=body.text,
        fb_caption=body.fb_caption, ig_caption=body.ig_caption,
        decision_status="edited",
    )


@app.get("/api/v1/facebook/groups", response_model=FacebookGroupsResponse)
def list_facebook_groups() -> FacebookGroupsResponse:
    """List all Facebook groups and their statuses."""
    groups_file = settings.paths.groups_tracker
    if not groups_file.exists():
        return FacebookGroupsResponse(groups=[], total=0, as_of=rh.now_iso())
    try:
        import json
        data = json.loads(groups_file.read_text(encoding="utf-8"))
        groups = [FacebookGroup.model_validate(g) for g in data]
        return FacebookGroupsResponse(groups=groups, total=len(groups), as_of=rh.now_iso())
    except Exception as exc:
        _log.error("Failed to read groups tracker: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to read groups tracker")

@app.put("/api/v1/facebook/groups/{group_name}", response_model=FacebookGroup)
def update_facebook_group(group_name: str, body: FacebookGroupUpdateBody) -> FacebookGroup:
    """Update a Facebook group's status."""
    groups_file = settings.paths.groups_tracker
    if not groups_file.exists():
        raise HTTPException(status_code=404, detail="groups tracker not found")
    import json
    data = json.loads(groups_file.read_text(encoding="utf-8"))
    
    for g in data:
        if g.get("group_name") == group_name:
            if body.status is not None:
                g["status"] = body.status
            if body.posting_mode is not None:
                g["posting_mode"] = body.posting_mode
            groups_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return FacebookGroup.model_validate(g)
            
    raise HTTPException(status_code=404, detail=f"group {group_name} not found")


@app.get("/api/v1/health")
def health() -> Response:
    """Liveness probe for launchd / curl. 204 = OK, no body needed."""
    return Response(status_code=204)


if __name__ == "__main__":  # pragma: no cover - manual run path
    import uvicorn

    host = os.getenv("WEB_UI_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_UI_PORT", "5001"))
    _log.info("starting approval_api on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
