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
# publish → enriching → approved / skipped → drafting → wp_draft → wp_published
# → social_queued → social_done
#
# `drafting` -- transient claim state set by `claim_idea_for_drafting()` the
# instant either the FastAPI background task or the cron safety-net worker
# starts drafting an approved idea. Its sole purpose is the atomic
# `WHERE status = 'approved'` guard that lets exactly one of those two
# concurrent callers win the race for a given idea; it is appended at the
# end of this tuple (not inserted mid-sequence) because `STATUSES` is only
# ever consumed as a set (`api/ideas_api.py`'s `VALID_STATUSES`), so member
# order carries no behavioral meaning -- appending minimizes diff noise.
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
    "drafting",
    "composing_reel",
)

# The terminal failure statuses -- the only ones that carry a `failure_reason`
# (see update_status). Kept as a set here rather than inline so the pipeline
# and the API agree on what "failed" means without restating the pair.
FAILURE_STATUSES = frozenset({"write_failed", "validation_failed"})


# ─────────────────────────────────────────────────────────────────────────────
# Write


def insert_idea(
    idea: dict[str, Any], *, brand_id: str | None = None, brand_name: str | None = None
) -> str | None:
    """Insert one idea row. Returns the new ``id`` or None on error.

    ``idea`` keys match Google Sheet columns (case-insensitive):
        Category, Topic, Target_Keyword, Persona_Context, Post_Goal, Status, Input

    ``Persona_Context`` was ``Nalla_Context`` before the de-brand. Both legacy
    spellings are still accepted here so a caller that has not been updated (or
    a queued payload written by an older container) still lands its value --
    but the value is always WRITTEN to the ``persona_context`` column.

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
            "persona_context": (
                idea.get("Persona_Context")
                or idea.get("persona_context")
                or idea.get("Nalla_Context")
                or idea.get("nalla_context")
            ),
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


def update_status(idea_id: str, status: str, failure_reason: str | None = None) -> bool:
    """Update the status of an existing idea. Returns True on success.

    `failure_reason` is persisted only for the terminal failure statuses
    (`write_failed`/`validation_failed`); for any other status it is forced
    to NULL. That clearing is the point, not a side effect: requeuing a
    failed idea back to `approved` must not leave last run's reason on the
    row, or the UI would show a stale cause for an idea that is now healthy.
    Callers that pass a reason with a non-failure status are ignored rather
    than errored -- this is a diagnostics field, never a control path.
    """
    reason = failure_reason if status in FAILURE_STATUSES else None
    try:
        db.execute(
            "UPDATE content_ideas SET status = %s, failure_reason = %s, "
            "updated_at = NOW() WHERE id = %s",
            (status, reason, idea_id),
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


def mark_wp_published(idea_id: str) -> bool:
    """Atomically transition idea_id from status='wp_draft' to status='wp_published',
    ONLY if it is still 'wp_draft' at the moment of the update. Returns True if this
    call made the transition. Called by the reels pipeline's detect-publish sweep
    once a CrewAI-drafted post is confirmed live via the WordPress REST API.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_ideas SET status = 'wp_published', updated_at = NOW() "
            "WHERE id = %s AND status = 'wp_draft'",
            (idea_id,),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("ideas_db.mark_wp_published failed: %s", exc)
        return False


def claim_idea_for_reel_composition(idea_id: str) -> bool:
    """Atomically transition idea_id from status='wp_published' to status='composing_reel',
    ONLY if it is still 'wp_published' at the moment of the update. Returns True if THIS
    call won the claim. Prevents two overlapping crewai_reels_pipeline.py runs from both
    composing the same idea's reel concurrently -- same role as claim_idea_for_drafting.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_ideas SET status = 'composing_reel', updated_at = NOW() "
            "WHERE id = %s AND status = 'wp_published'",
            (idea_id,),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("ideas_db.claim_idea_for_reel_composition failed: %s", exc)
        return False


def set_reel_pending_review(
    idea_id: str,
    *,
    ig_video_path: str,
    fb_video_path: str,
    ig_caption: str,
    fb_caption: str,
    source: str,
    validation_flags: list[str] | None = None,
) -> bool:
    """Store composed reel artifacts and move the idea to status='social_queued'
    for human review, ONLY if it is still 'composing_reel'. `ig_video_path` and
    `fb_video_path` are the same file when `source='openart'` (one shared clip),
    distinct files when `source='fallback'` (two platform-tuned slideshow renders).
    """
    try:
        rowcount = db.execute(
            "UPDATE content_ideas SET status = 'social_queued', "
            "reel_ig_video_path = %s, reel_fb_video_path = %s, "
            "reel_ig_caption = %s, reel_fb_caption = %s, reel_source = %s, "
            "reel_validation_flags = %s, updated_at = NOW() "
            "WHERE id = %s AND status = 'composing_reel'",
            (
                ig_video_path,
                fb_video_path,
                ig_caption,
                fb_caption,
                source,
                validation_flags or None,
                idea_id,
            ),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("ideas_db.set_reel_pending_review failed: %s", exc)
        return False


def reject_reel(idea_id: str) -> bool:
    """Reject a pending reel: atomically move idea_id from status='social_queued'
    back to status='wp_published', clearing the pending-review columns so a
    rejected reel doesn't linger in the review UI. The idea itself is untouched
    and could be re-run through the reels pipeline later.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_ideas SET status = 'wp_published', "
            "reel_ig_video_path = NULL, reel_fb_video_path = NULL, "
            "reel_ig_caption = NULL, reel_fb_caption = NULL, reel_source = NULL, "
            "reel_validation_flags = NULL, updated_at = NOW() "
            "WHERE id = %s AND status = 'social_queued'",
            (idea_id,),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("ideas_db.reject_reel failed: %s", exc)
        return False


def set_reel_result(
    idea_id: str, *, ig_reel_url: str | None = None, fb_reel_url: str | None = None
) -> bool:
    """Store the live IG/FB reel URL(s). Supports being called once per platform
    independently (partial-publish-failure retry case) -- only the columns for
    whichever URL is passed get overwritten (COALESCE keeps the other as-is).
    Flips status='social_done' only once BOTH ig_reel_url and fb_reel_url are
    non-null on the row (either just-set here or already set from an earlier
    partial call) -- otherwise the idea stays at 'social_queued' so a retry from
    the review page still finds it.
    """
    try:
        db.execute(
            "UPDATE content_ideas SET "
            "ig_reel_url = COALESCE(%s, ig_reel_url), "
            "fb_reel_url = COALESCE(%s, fb_reel_url), "
            "updated_at = NOW() WHERE id = %s",
            (ig_reel_url, fb_reel_url, idea_id),
        )
        rowcount = db.execute(
            "UPDATE content_ideas SET status = 'social_done', updated_at = NOW() "
            "WHERE id = %s AND status = 'social_queued' "
            "AND ig_reel_url IS NOT NULL AND fb_reel_url IS NOT NULL",
            (idea_id,),
        )
        return rowcount >= 0
    except Exception as exc:
        _log.warning("ideas_db.set_reel_result failed: %s", exc)
        return False


def claim_idea_for_drafting(idea_id: str) -> bool:
    """Atomically transition idea_id from status='approved' to status='drafting',
    ONLY if it is still 'approved' at the moment of the update. Returns True if
    THIS call won the claim (i.e. is now responsible for drafting it), False if
    the idea was not found, was already claimed/moved to a different status by
    someone else (e.g. a "Disable" click, or a concurrent claim), or on any DB
    error. This is the sole mechanism preventing two concurrent callers (an API
    background task and a cron safety-net worker) from both spawning a drafting
    subprocess for the same idea.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_ideas SET status = 'drafting', updated_at = NOW() "
            "WHERE id = %s AND status = 'approved'",
            (idea_id,),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("ideas_db.claim_idea_for_drafting failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Read


def get_idea(idea_id: str) -> dict[str, Any] | None:
    """Fetch a single idea by id, or None if it doesn't exist (or on error)."""
    try:
        return db.fetch_one("SELECT * FROM content_ideas WHERE id = %s", (idea_id,))
    except Exception as exc:
        _log.warning("ideas_db.get_idea failed: %s", exc)
        return None


def list_ideas(
    *,
    status: str | None = None,
    brand_id: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return ideas filtered by status and/or brand, newest first.

    Raises (unlike the write helpers, which degrade to a False return) --
    an empty list here is a factual claim that the brand has no matching
    ideas, and callers act on it: `worker_wp_ideas.py` logs
    "no_approved_ideas_to_draft" and exits 0. Swallowing a DB failure into
    `[]` therefore reports a healthy no-op run while the queue silently
    backs up: that ran 4x/day from 2026-08-10 to 2026-08-14 on a launchd
    job with no DATABASE_URL, every run stamped [success], never once
    seeing an approved idea.
    """
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
