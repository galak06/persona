"""Content ideas repository — Supabase ``content_ideas`` table.

Replaces Google Sheet "posts" tab as the storage layer for the content-ideator
skill. Module-level helpers are defensive (never raise) so an ideas-logging
failure never breaks a run.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from postgrest.types import CountMethod

from lib.supabase_client import get_client

_log = logging.getLogger(__name__)

# Status lifecycle (mirrors the old sheet Status column)
# publish → enriching → approved / skipped → wp_draft → wp_published
# → social_queued → social_done
STATUSES = (
    "publish", "enriching", "approved", "skipped",
    "wp_draft", "wp_published", "social_queued", "social_done",
)


def _rows(data: object) -> list[dict[str, Any]]:
    """Safely cast supabase-py result.data to list[dict]."""
    return cast(list[dict[str, Any]], data) if isinstance(data, list) else []


# ─────────────────────────────────────────────────────────────────────────────
# Write

def insert_idea(idea: dict[str, Any], *, brand_id: str | None = None) -> str | None:
    """Insert one idea row. Returns the new ``id`` or None on error.

    ``idea`` keys match Google Sheet columns (case-insensitive):
        Category, Topic, Target_Keyword, Nalla_Context, Post_Goal, Status, Input
    """
    try:
        row: dict[str, Any] = {
            "category":       idea.get("Category") or idea.get("category", ""),
            "topic":          idea.get("Topic") or idea.get("topic", ""),
            "target_keyword": idea.get("Target_Keyword") or idea.get("target_keyword"),
            "nalla_context":  idea.get("Nalla_Context") or idea.get("nalla_context"),
            "post_goal":      idea.get("Post_Goal") or idea.get("post_goal"),
            "status":         idea.get("Status") or idea.get("status") or "publish",
            "input":          idea.get("Input") or idea.get("input"),
        }
        if brand_id:
            row["brand_id"] = brand_id
        result = (
            get_client()
            .table("content_ideas")
            .upsert(row, on_conflict="lower(topic),COALESCE(brand_id, '')")
            .execute()
        )
        rows = _rows(result.data)
        return str(rows[0]["id"]) if rows else None
    except Exception as exc:
        _log.warning("ideas_db.insert_idea failed: %s", exc)
        return None


def update_status(idea_id: str, status: str) -> bool:
    """Update the status of an existing idea. Returns True on success."""
    try:
        get_client().table("content_ideas").update({"status": status}).eq("id", idea_id).execute()
        return True
    except Exception as exc:
        _log.warning("ideas_db.update_status failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Read

def list_ideas(
    *,
    status: str | None = None,
    brand_id: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return ideas filtered by status and/or brand, newest first."""
    try:
        q = (
            get_client()
            .table("content_ideas")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if status:
            q = q.eq("status", status)
        if brand_id:
            q = q.eq("brand_id", brand_id)
        return _rows(q.execute().data)
    except Exception as exc:
        _log.warning("ideas_db.list_ideas failed: %s", exc)
        return []


def existing_topics(*, brand_id: str | None = None) -> set[str]:
    """Return all known topic strings (lowercased) for dedup checks."""
    try:
        q = get_client().table("content_ideas").select("topic")
        if brand_id:
            q = q.eq("brand_id", brand_id)
        rows = _rows(q.execute().data)
        return {str(r["topic"]).lower() for r in rows if r.get("topic")}
    except Exception as exc:
        _log.warning("ideas_db.existing_topics failed: %s", exc)
        return set()


def pending_count(*, brand_id: str | None = None) -> int:
    """Count ideas with status='publish' — drives the 'need more ideas' trigger."""
    try:
        q = (
            get_client()
            .table("content_ideas")
            .select("id", count=CountMethod.exact)
            .eq("status", "publish")
        )
        if brand_id:
            q = q.eq("brand_id", brand_id)
        return q.execute().count or 0
    except Exception as exc:
        _log.warning("ideas_db.pending_count failed: %s", exc)
        return 0
