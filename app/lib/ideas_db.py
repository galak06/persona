"""Content ideas repository — local Postgres ``content_ideas`` table.

Replaces the Google Sheet "posts" tab and Supabase's ``content_ideas`` table
as the storage layer for the content-ideator skill and GSC scout, now that
the Supabase project's DNS is permanently unreachable. Reads/writes go
through ``lib.db`` (pooled psycopg connections), same as
``published_content_db`` / ``engagements_db``.

Module-level helpers are defensive (never raise) so an ideas-logging failure
never breaks a run.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from lib import db

_log = logging.getLogger(__name__)

# Status lifecycle (mirrors the old sheet Status column)
# publish → enriching → approved / skipped → wp_draft → wp_published
# → social_queued → social_done
#
# `write_failed`/`validation_failed` are CrewAI-pipeline-specific
# (lib.crew.writer.orchestrator / scripts/crewai_content_pipeline.py), not
# part of the original sheet lifecycle -- distinct from `skipped`, which is
# an editorial rejection (content-enricher's human/Telegram approval flow).
# Both move the idea off `status='publish'` so `select_idea`'s deterministic
# highest-scored-first pick doesn't retry the exact same idea forever on
# every subsequent run (live-reproduced: consecutive pipeline runs picking
# the same idea and failing the same way, at two different stages):
#   write_failed      -- strategist/writer technically failed to produce
#                         valid structured output (e.g. malformed JSON)
#   validation_failed -- a real post WAS generated but rejected by
#                         lib.crew.validate (medical-claims gate or quality
#                         editor) -- never set on a --dry-run run, which is
#                         an explicitly side-effect-free preview
STATUSES = (
    "publish",
    "enriching",
    "approved",
    "skipped",
    "wp_draft",
    "wp_published",
    "social_queued",
    "social_done",
    "write_failed",
    "validation_failed",
)


# ─────────────────────────────────────────────────────────────────────────────
# Write


def insert_idea(
    idea: dict[str, Any], *, brand_id: str | None = None, brand_name: str | None = None
) -> str | None:
    """Insert one idea row. Returns the new ``id`` or None on error.

    ``idea`` keys match Google Sheet columns (case-insensitive):
        Category, Topic, Target_Keyword, Nalla_Context, Post_Goal, Status, Input

    Plain INSERT, not an upsert -- a true duplicate ``(lower(topic), brand_id)``
    trips the unique index and is caught below, returning None (matching the
    old Supabase ``.insert()``, which would also have raised/failed on that
    same unique-constraint violation).
    """
    try:
        row: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "category": idea.get("Category") or idea.get("category", ""),
            "topic": idea.get("Topic") or idea.get("topic", ""),
            "target_keyword": idea.get("Target_Keyword") or idea.get("target_keyword"),
            "nalla_context": idea.get("Nalla_Context") or idea.get("nalla_context"),
            "post_goal": idea.get("Post_Goal") or idea.get("post_goal"),
            "status": idea.get("Status") or idea.get("status") or "publish",
            "input": idea.get("Input") or idea.get("input"),
        }
        if brand_id:
            row["brand_id"] = brand_id
        if brand_name:
            row["brand_name"] = brand_name

        columns = list(row.keys())
        insert_cols = ", ".join(columns)
        placeholders = ", ".join(f"%({c})s" for c in columns)
        query = f"INSERT INTO content_ideas ({insert_cols}) VALUES ({placeholders})"
        db.execute(query, row)
        return str(row["id"])
    except Exception as exc:
        _log.warning("ideas_db.insert_idea failed: %s", exc)
        return None


def update_status(idea_id: str, status: str) -> bool:
    """Update the status of an existing idea. Returns True on success."""
    try:
        db.execute(
            "UPDATE content_ideas SET status = %s, updated_at = NOW() WHERE id = %s",
            (status, idea_id),
        )
        return True
    except Exception as exc:
        _log.warning("ideas_db.update_status failed: %s", exc)
        return False


def set_wp_result(idea_id: str, wp_post_id: str, wp_url: str) -> bool:
    """Store the WordPress post ID and URL on a published idea."""
    try:
        rowcount = db.execute(
            "UPDATE content_ideas SET wp_post_id = %s, wp_url = %s, updated_at = NOW() WHERE id = %s",
            (wp_post_id, wp_url, idea_id),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("ideas_db.set_wp_result failed: %s", exc)
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
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = %s")
            params.append(status)
        if brand_id:
            clauses.append("brand_id = %s")
            params.append(brand_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 5000)))
        query = f"SELECT * FROM content_ideas{where} ORDER BY created_at DESC LIMIT %s"
        return db.fetch_all(query, tuple(params))
    except Exception as exc:
        _log.warning("ideas_db.list_ideas failed: %s", exc)
        return []


def existing_topics(*, brand_id: str | None = None) -> set[str]:
    """Return all known topic strings (lowercased) for dedup checks."""
    try:
        if brand_id:
            rows = db.fetch_all("SELECT topic FROM content_ideas WHERE brand_id = %s", (brand_id,))
        else:
            rows = db.fetch_all("SELECT topic FROM content_ideas", None)
        return {str(r["topic"]).lower() for r in rows if r.get("topic")}
    except Exception as exc:
        _log.warning("ideas_db.existing_topics failed: %s", exc)
        return set()


def pending_count(*, brand_id: str | None = None) -> int:
    """Count ideas with status='publish' — drives the 'need more ideas' trigger."""
    try:
        if brand_id:
            row = db.fetch_one(
                "SELECT COUNT(*) AS n FROM content_ideas WHERE status = %s AND brand_id = %s",
                ("publish", brand_id),
            )
        else:
            row = db.fetch_one(
                "SELECT COUNT(*) AS n FROM content_ideas WHERE status = %s", ("publish",)
            )
        return int(row["n"]) if row else 0
    except Exception as exc:
        _log.warning("ideas_db.pending_count failed: %s", exc)
        return 0
