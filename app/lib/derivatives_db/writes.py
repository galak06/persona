"""Write side of ``content_derivatives`` up to (not including) publishing.

CONTRACT FROZEN BY PHASE 0 -- signatures, return types and the SQL guard each
docstring spells out are final. Phase 1A replaces the ``NotImplementedError``
bodies (and drops the ``# noqa: F401`` markers once a body uses the import) and
changes nothing else. The publish claim and result writes live in
``publish.py``; together the two files are the whole state machine.

Every transition is ONE conditional statement: ``UPDATE content_derivatives SET
status = <next>, ..., updated_at = NOW() WHERE id = %s AND status = <prior>``.
"Did it work?" is ``rowcount > 0``, never a read followed by a write -- a
read-then-write guard is exactly the double-post race this table exists to
close (see the comment above the table in ``db/schema.sql``).

Style to mirror exactly: ``lib/social_post_db.py``. Every function

* goes through ``lib.db`` as ``db.execute`` so tests patch
  ``lib.derivatives_db.writes.db.execute``;
* is DEFENSIVE: ``try/except Exception`` -> ``_log.warning("derivatives_db.<fn>
  failed: %s", exc)`` -> the falsy return (``False`` / ``None`` / ``0``). It
  NEVER raises, so a bookkeeping failure cannot take a compose or publish run
  down with it;
* returns ``True`` only when THIS call moved the row.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from lib import db
from lib.derivatives_db.new_derivative import NewDerivative
from lib.derivatives_db.statuses import (
    KIND_SPOTLIGHT,
    STALE_COMPOSING_ERROR,
    STALE_COMPOSING_SECONDS,
)

_log = logging.getLogger(__name__)


def insert_composing(new: NewDerivative) -> str | None:
    """Create the row in ``'composing'``. Returns the new id, or ``None``.

    ``None`` means EITHER "an active row already exists for this (idea,
    product, format)" OR "the insert failed" (FK violation, database down). The
    caller tells them apart with ``reads.find_active``: found -> duplicate
    (409 with that id), not found -> real failure (500).

    The id is ``str(uuid.uuid4())``, minted here -- the ``content_ideas.id``
    convention. ``kind`` is written explicitly as ``statuses.KIND_SPOTLIGHT``.

    SQL (the ``ON CONFLICT`` predicate must match the partial unique index
    ``uq_content_derivatives_active`` byte for byte, or Postgres will not infer
    it as the arbiter and the statement errors instead of doing nothing)::

        INSERT INTO content_derivatives
            (id, idea_id, brand_id, kind, format, product_key, product_asin,
             product_display, reference_category, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'composing')
        ON CONFLICT (idea_id, product_key, format)
            WHERE status IN ('composing', 'queued', 'scheduled')
        DO NOTHING

    ``rowcount == 0`` -> ``None``; otherwise the minted id.
    """
    try:
        derivative_id = str(uuid.uuid4())
        rowcount = db.execute(
            "INSERT INTO content_derivatives "
            "(id, idea_id, brand_id, kind, format, product_key, product_asin, "
            "product_display, reference_category, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'composing') "
            "ON CONFLICT (idea_id, product_key, format) "
            "WHERE status IN ('composing', 'queued', 'scheduled') "
            "DO NOTHING",
            (
                derivative_id,
                new.idea_id,
                new.brand_id,
                KIND_SPOTLIGHT,
                new.format,
                new.product_key,
                new.product_asin,
                new.product_display,
                new.reference_category,
            ),
        )
        return derivative_id if rowcount > 0 else None
    except Exception as exc:
        _log.warning("derivatives_db.insert_composing failed: %s", exc)
        return None


def set_pending_review(
    derivative_id: str,
    *,
    fb_caption: str,
    ig_caption: str,
    comment_keyword: str,
    image_path: str,
    image_alt: str,
    source: str,
    validation_flags: list[str] | None,
    fb_affiliate_url: str,
    ig_affiliate_url: str,
) -> bool:
    """Store the composed spotlight and move ``'composing' -> 'queued'``.

    ``image_path`` must be RELATIVE to ``$BRAND_DIR`` (what
    ``lib.crew.socialpost.compose.compose_image`` returns) so the worker that
    composed it and the API container that serves it resolve the same
    bind-mounted file. ``validation_flags`` is stored as ``NULL`` when empty
    (``validation_flags or None``), matching ``social_post_db``.

    Guard: ``WHERE id = %s AND status = 'composing'``. ``False`` means the row
    was failed or swept as stale while this run was composing; the CALLER then
    unlinks the image it just wrote, so a refused write leaves no orphan file.
    Also clears ``error`` (``error = NULL``).
    """
    try:
        rowcount = db.execute(
            "UPDATE content_derivatives SET status = 'queued', "
            "fb_caption = %s, ig_caption = %s, comment_keyword = %s, "
            "image_path = %s, image_alt = %s, source = %s, validation_flags = %s, "
            "fb_affiliate_url = %s, ig_affiliate_url = %s, error = NULL, "
            "updated_at = NOW() "
            "WHERE id = %s AND status = 'composing'",
            (
                fb_caption,
                ig_caption,
                comment_keyword,
                image_path,
                image_alt,
                source,
                validation_flags or None,
                fb_affiliate_url,
                ig_affiliate_url,
                derivative_id,
            ),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("derivatives_db.set_pending_review failed: %s", exc)
        return False


def mark_failed(derivative_id: str, *, error: str) -> bool:
    """Terminal failure of a composition: ``'composing' -> 'failed'``.

    ``error`` is a stable machine-readable reason (``wp_fetch_failed``,
    ``plan_failed``, ``no_reference_photo``, ``dispatch_failed`` ...) shown on
    the review card. Store ``error[:500]``.

    Guard: ``WHERE id = %s AND status = 'composing'`` -- a row that already
    reached ``'queued'`` can never be failed retroactively by a late or
    duplicate run. 'failed' is outside the unique index, so the owner can
    create the same spotlight again immediately.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_derivatives SET status = 'failed', error = %s, updated_at = NOW() "
            "WHERE id = %s AND status = 'composing'",
            (error[:500], derivative_id),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("derivatives_db.mark_failed failed: %s", exc)
        return False


def fail_stale_composing(
    *, brand_id: str, older_than_seconds: int = STALE_COMPOSING_SECONDS
) -> int:
    """Fail every ``'composing'`` row of ``brand_id`` that stopped moving.

    A compose run killed mid-flight (worker restart, container recreate) never
    reports, and its row would otherwise block re-creation of that spotlight
    forever through the unique index. Called by the create AND list routes, so
    no scheduler is needed. Returns the number of rows failed (``0`` on error).

    Uses the DATABASE clock on both sides -- the API container, the worker and
    Postgres do not necessarily agree on wall-clock::

        UPDATE content_derivatives
        SET status = 'failed', error = %s, updated_at = NOW()
        WHERE status = 'composing' AND brand_id = %s
          AND updated_at < NOW() - make_interval(secs => %s)

    with ``statuses.STALE_COMPOSING_ERROR`` and ``float(older_than_seconds)``.
    """
    try:
        return db.execute(
            "UPDATE content_derivatives SET status = 'failed', error = %s, updated_at = NOW() "
            "WHERE status = 'composing' AND brand_id = %s "
            "AND updated_at < NOW() - make_interval(secs => %s)",
            (STALE_COMPOSING_ERROR, brand_id, float(older_than_seconds)),
        )
    except Exception as exc:
        _log.warning("derivatives_db.fail_stale_composing failed: %s", exc)
        return 0


def reject(derivative_id: str) -> bool:
    """Reject a reviewed spotlight: ``'queued' -> 'rejected'``, terminal.

    Guard: ``WHERE id = %s AND status = 'queued'``. The API unlinks the image
    afterwards; this function touches only the row.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_derivatives SET status = 'rejected', updated_at = NOW() "
            "WHERE id = %s AND status = 'queued'",
            (derivative_id,),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("derivatives_db.reject failed: %s", exc)
        return False


def schedule_fb(derivative_id: str, *, due_at: datetime) -> bool:
    """Approve into a Facebook slot: ``'queued' -> 'scheduled'``.

    Sets ``fb_due_at = %s``. ``due_at`` comes from
    ``lib.social_slot_allocator.next_shared_fb_slot``. Guard: ``WHERE id = %s
    AND status = 'queued'`` so a double click cannot re-slot a scheduled row.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_derivatives SET status = 'scheduled', "
            "fb_due_at = %s, updated_at = NOW() "
            "WHERE id = %s AND status = 'queued'",
            (due_at, derivative_id),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("derivatives_db.schedule_fb failed: %s", exc)
        return False


def unschedule_fb(derivative_id: str) -> bool:
    """Release the slot and return to review: ``'scheduled' -> 'queued'``.

    Sets ``fb_due_at = NULL``. Guard: ``WHERE id = %s AND status =
    'scheduled'`` -- once the worker has claimed ``'fb_publishing'`` the post
    can no longer be pulled back, which is the point of the claim.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_derivatives SET status = 'queued', "
            "fb_due_at = NULL, updated_at = NOW() "
            "WHERE id = %s AND status = 'scheduled'",
            (derivative_id,),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("derivatives_db.unschedule_fb failed: %s", exc)
        return False
