# pyright: reportMissingImports=false
"""Internal helpers for ``api/approval_api.py`` route handlers.

Split out to keep the route module under the 300-line cap. These helpers
contain the dispatch-on-type plumbing that ``approve_item`` /
``reject_item`` / ``edit_item`` share — pure functions modulo the API
state mutation, so easy to unit-test in isolation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, HTTPException, status

from api import state
from api.schemas import BlogPostItem, CommentItem, DecisionResponse
from lib import groups_queue
from lib.config import settings

__all__ = [
    "BLOG_POST_QUEUE_PATH",
    "COMMENT_QUEUE_PATH",
    "approve_blog_post",
    "approve_group",
    "classify",
    "dispatch_join_request",
    "now_iso",
    "pending_only",
    "queue_for_id",
    "to_blog_post",
    "to_comment",
]

_REPO_ROOT: Path = Path(__file__).resolve().parent.parent
COMMENT_QUEUE_PATH: Path = settings.paths.comment_queue
BLOG_POST_QUEUE_PATH: Path = settings.paths.state_dir / "blog_post_queue.json"

_log = logging.getLogger("approval_api")


def now_iso() -> str:
    """ISO-8601 UTC timestamp with tz suffix."""
    return datetime.now(UTC).isoformat()


def classify(item: dict[str, Any]) -> str:
    """Return ``"blog_post"``, ``"comment"``, or ``"group_to_join"``."""
    explicit = item.get("type")
    if isinstance(explicit, str) and explicit in ("comment", "blog_post", "group_to_join"):
        return explicit
    if "fb_caption" in item or "ig_caption" in item or "post_title" in item:
        return "blog_post"
    if "found_via_query" in item or "member_count" in item:
        return "group_to_join"
    return "comment"


def to_blog_post(item: dict[str, Any]) -> BlogPostItem:
    """Coerce a raw blog-post queue dict into the pydantic model."""
    payload = dict(item)
    payload["type"] = "blog_post"
    return BlogPostItem.model_validate(payload)


def to_comment(item: dict[str, Any]) -> CommentItem:
    """Coerce a raw comment queue dict into the pydantic model (legacy only)."""
    payload = dict(item)
    payload["type"] = "comment"
    payload.setdefault(
        "group_or_hashtag",
        item.get("group_name") or item.get("hashtag"),
    )
    return CommentItem.model_validate(payload)


def queue_for_id(item_id: str) -> tuple[str, Path | None, dict[str, Any]] | None:
    """Locate an item across blog-post + comment + groups queues.

    Returns ``(kind, path, raw)`` where ``path`` is the on-disk JSON file
    for blog/comment queues. For group items ``path`` is ``None`` — commits
    go through ``lib.groups_queue.commit_group_decision``. Returns ``None``
    if no queue has the id.
    """
    for path, kind in (
        (BLOG_POST_QUEUE_PATH, "blog_post"),
        (COMMENT_QUEUE_PATH, "comment"),
    ):
        found = state.find_item(path, item_id)
        if found is not None:
            return kind, path, found
    for group in groups_queue.read_pending_groups():
        if group.id == item_id:
            return "group_to_join", None, group.model_dump()
    return None


def pending_only(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter to items still awaiting a decision."""
    return [
        item
        for item in items
        if not item.get("decided_by") and item.get("status") in ("pending", None, "")
    ]


def approve_comment(
    path: Path,
    item_id: str,
    *,
    text: str | None,
    decision_status: Literal["approved", "USER_SKIPPED", "edited"],
) -> DecisionResponse:
    """Shared commit path for comment approve / reject / edit."""
    decided_at = now_iso()
    result = state.commit_decision(
        path,
        item_id,
        status=decision_status,
        decided_by="web_ui",
        decided_at=decided_at,
        text=text,
    )
    if result == "not_found":
        raise HTTPException(status_code=404, detail=f"item {item_id} not found")
    if result == "already_decided":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"item {item_id} was already decided by another channel",
        )
    _log.info(
        '{"event": "comment_decision", "id": "%s", "status": "%s"}',
        item_id, decision_status,
    )
    return DecisionResponse(
        id=item_id, status=decision_status, decided_by="web_ui", decided_at=decided_at,
    )


def approve_blog_post(
    path: Path,
    item_id: str,
    *,
    channel: str | None,
    text: str | None,
    fb_caption: str | None,
    ig_caption: str | None,
    decision_status: Literal["approved", "USER_SKIPPED", "edited"],
) -> DecisionResponse:
    """Shared commit path for blog_post approve / reject / edit."""
    decided_at = now_iso()
    result = state.commit_decision(
        path,
        item_id,
        status=decision_status,
        decided_by="web_ui",
        decided_at=decided_at,
        channel=channel,
        text=text,
        fb_caption=fb_caption,
        ig_caption=ig_caption,
    )
    if result == "not_found":
        raise HTTPException(status_code=404, detail=f"item {item_id} not found")
    if result == "already_decided":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"item {item_id} was already decided by another channel",
        )
    _log.info(
        '{"event": "blog_post_decision", "id": "%s", "status": "%s", "channel": "%s"}',
        item_id, decision_status, channel,
    )
    return DecisionResponse(
        id=item_id, status=decision_status, decided_by="web_ui", decided_at=decided_at,
    )


def dispatch_join_request(group: dict[str, Any]) -> None:
    """Background-task wrapper for ``send_join_requests``.

    The Playwright-driven join needs a logged-in browser ``page``, which
    only exists inside ``scripts/fb_group_scout.py``. From the API process
    we cannot spin one up safely — record the queued decision and let the
    next ``fb_group_scout`` cron run pick up the approved rows.
    """
    try:
        _log.info(
            '{"event": "group_join_deferred_to_cron", "id": "%s", "url": "%s"}',
            group.get("id"), group.get("url"),
        )
    except (TypeError, ValueError) as exc:
        _log.warning("join dispatch logging failed: %s", exc)


def approve_group(
    raw: dict[str, Any],
    *,
    status_value: Literal["approved", "USER_SKIPPED"],
    background_tasks: BackgroundTasks,
) -> DecisionResponse:
    """Commit a group-join decision; on approve, queue the join request.

    The 5/day, 15/week cap is checked *before* committing so the user
    sees a clean 429 rather than a committed-then-dropped decision.
    """
    group_id = raw["id"]
    if status_value == "approved":
        allowed, reason = groups_queue.under_join_cap()
        if not allowed:
            _log.info(
                '{"event": "group_join_capped", "id": "%s", "reason": "%s"}',
                group_id, reason,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"join cap reached: {reason}",
            )

    decided_at = now_iso()
    result = groups_queue.commit_group_decision(
        group_id,
        status=status_value,
        decided_by="web_ui",
        decided_at=decided_at,
    )
    if result == "not_found":
        raise HTTPException(status_code=404, detail=f"item {group_id} not found")
    if result == "already_decided":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"item {group_id} was already decided by another channel",
        )

    join_status: Literal["queued"] | None = None
    if status_value == "approved":
        background_tasks.add_task(dispatch_join_request, raw)
        join_status = "queued"
        _log.info('{"event": "group_join_queued", "id": "%s"}', group_id)
    else:
        _log.info('{"event": "group_skipped", "id": "%s"}', group_id)

    return DecisionResponse(
        id=group_id,
        status=status_value,
        decided_by="web_ui",
        decided_at=decided_at,
        join_status=join_status,
    )


