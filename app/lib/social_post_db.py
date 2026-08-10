r"""Social-post track on the ``content_ideas`` table (FB Page post + IG feed post).

A sibling of ``lib.ideas_db``, not part of it: that module is already at the
file-size limit, and this is a genuinely separate concern. Both operate on the
same row, the way the reels columns and the core idea columns already do.

The two tracks are independent by design. Reels drive the shared ``status``
column; this one drives ``social_post_status`` and never touches ``status``.
That is what lets one idea be reeled and posted in either order, or only one
of the two -- and why an idea already at ``status='social_done'`` (reel
published) is still a valid candidate here.

Lifecycle::

    NULL -> 'composing' -> 'queued' --approve--> 'fb_published' -> 'published'
                        |                                      ^
                        \-> 'rejected' (terminal)              |
                                                    IG released once
                                             social_post_ig_due_at has passed

``'fb_published'`` is a resting state, not a transient. Facebook publishes on
approval; Instagram waits out the documented FB<->IG gap before it is eligible.

Every helper is defensive (logs, never raises) -- same posture as
``lib.ideas_db`` -- so a bookkeeping failure never takes down a publish run.
"""

from __future__ import annotations

import logging
from typing import Any

from lib import db

_log = logging.getLogger(__name__)

STATUSES = ("composing", "queued", "fb_published", "published", "rejected")

# Ideas whose reel track has reached any of these are still postable -- the
# social-post track doesn't care how far the reel got.
_ELIGIBLE_IDEA_STATUSES = ("wp_published", "social_done")


# ─────────────────────────────────────────────────────────────────────────────
# Read


def list_candidates(*, brand_id: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """Ideas with a live WP post and no social-post track started yet.

    ``social_post_status IS NULL`` is what makes 'rejected' terminal: a
    rejected row keeps a non-NULL status and so never comes back here.
    """
    try:
        params: list[Any] = [list(_ELIGIBLE_IDEA_STATUSES)]
        clause = ""
        if brand_id:
            clause = " AND brand_id = %s"
            params.append(brand_id)
        params.append(max(1, min(limit, 500)))
        return db.fetch_all(
            "SELECT * FROM content_ideas "
            "WHERE status = ANY(%s) AND wp_url IS NOT NULL AND social_post_status IS NULL"
            f"{clause} ORDER BY created_at DESC LIMIT %s",
            tuple(params),
        )
    except Exception as exc:
        _log.warning("social_post_db.list_candidates failed: %s", exc)
        return []


def list_due_for_ig(*, brand_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Rows whose FB half is published and whose IG gap has now elapsed."""
    try:
        params: list[Any] = []
        clause = ""
        if brand_id:
            clause = " AND brand_id = %s"
            params.append(brand_id)
        params.append(max(1, min(limit, 500)))
        return db.fetch_all(
            "SELECT * FROM content_ideas "
            "WHERE social_post_status = 'fb_published' "
            "AND social_post_ig_due_at IS NOT NULL AND social_post_ig_due_at <= NOW()"
            f"{clause} ORDER BY social_post_ig_due_at ASC LIMIT %s",
            tuple(params),
        )
    except Exception as exc:
        _log.warning("social_post_db.list_due_for_ig failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Write


def claim(idea_id: str) -> bool:
    """Atomically take ``NULL -> 'composing'``. True if THIS call won it.

    Guards two overlapping pipeline runs from composing the same idea twice --
    same role as ``ideas_db.claim_idea_for_reel_composition``.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_ideas SET social_post_status = 'composing', updated_at = NOW() "
            "WHERE id = %s AND social_post_status IS NULL",
            (idea_id,),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("social_post_db.claim failed: %s", exc)
        return False


def revert_claim(idea_id: str) -> bool:
    """Un-stick a failed composition (``'composing' -> NULL``) so the next run
    retries it instead of leaving it wedged forever."""
    try:
        rowcount = db.execute(
            "UPDATE content_ideas SET social_post_status = NULL, updated_at = NOW() "
            "WHERE id = %s AND social_post_status = 'composing'",
            (idea_id,),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("social_post_db.revert_claim failed: %s", exc)
        return False


def set_pending_review(
    idea_id: str,
    *,
    fb_caption: str,
    ig_caption: str,
    image_path: str,
    image_alt: str,
    source: str,
    validation_flags: list[str] | None = None,
) -> bool:
    """Store the composed post and move to ``'queued'`` for human review.

    ``image_path`` must be RELATIVE to ``$BRAND_DIR`` so the host-side composer
    and the containerized API resolve the same bind-mounted tree.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_ideas SET social_post_status = 'queued', "
            "social_post_fb_caption = %s, social_post_ig_caption = %s, "
            "social_post_image_path = %s, social_post_image_alt = %s, "
            "social_post_source = %s, social_post_validation_flags = %s, "
            "updated_at = NOW() "
            "WHERE id = %s AND social_post_status = 'composing'",
            (
                fb_caption,
                ig_caption,
                image_path,
                image_alt,
                source,
                validation_flags or None,
                idea_id,
            ),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("social_post_db.set_pending_review failed: %s", exc)
        return False


def reject(idea_id: str) -> bool:
    """Reject a queued post: ``'queued' -> 'rejected'``, terminal.

    Deliberately NOT a reset to NULL. ``ideas_db.reject_reel`` returns its row
    to a state the pipeline immediately re-selects, so a rejected reel is
    regenerated (new LLM + image calls) on the very next run, forever. A
    terminal value simply falls out of ``list_candidates``.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_ideas SET social_post_status = 'rejected', updated_at = NOW() "
            "WHERE id = %s AND social_post_status = 'queued'",
            (idea_id,),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("social_post_db.reject failed: %s", exc)
        return False


def set_fb_result(idea_id: str, *, url: str | None, ig_gap_hours: float) -> bool:
    """Record the live FB Page post and arm the IG half.

    ``social_post_ig_due_at`` is stamped by the DATABASE (``NOW() + interval``),
    not by Python -- the API container, the host pipeline and Postgres do not
    necessarily agree on wall-clock, and the gap only means anything relative to
    the moment the FB post actually went out.

    Guarded on ``'queued'`` so a double-approve can't re-arm an already-published
    row and push its IG half further into the future.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_ideas SET social_post_status = 'fb_published', "
            "fb_page_post_url = %s, "
            "social_post_ig_due_at = NOW() + make_interval(secs => %s), "
            "updated_at = NOW() "
            "WHERE id = %s AND social_post_status = 'queued'",
            (url, float(ig_gap_hours) * 3600.0, idea_id),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("social_post_db.set_fb_result failed: %s", exc)
        return False


def set_ig_result(idea_id: str, *, url: str | None) -> bool:
    """Record the live IG feed post: ``'fb_published' -> 'published'``."""
    try:
        rowcount = db.execute(
            "UPDATE content_ideas SET social_post_status = 'published', "
            "ig_post_url = %s, updated_at = NOW() "
            "WHERE id = %s AND social_post_status = 'fb_published'",
            (url, idea_id),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("social_post_db.set_ig_result failed: %s", exc)
        return False
