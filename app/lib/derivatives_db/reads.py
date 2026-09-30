"""Read side of ``content_derivatives``.

CONTRACT FROZEN BY PHASE 0 -- signatures, return types and the SQL each
docstring spells out are final. Phase 1A replaces the ``NotImplementedError``
bodies (and drops the ``# noqa: F401`` on the ``db`` import once a body uses
it) and changes nothing else.

Style to mirror exactly: ``lib/social_post_db.py``. Every function

* goes through ``lib.db`` (``db.fetch_one`` / ``db.fetch_all``), referenced as
  ``db.<fn>`` so tests patch ``lib.derivatives_db.reads.db.fetch_all``;
* is DEFENSIVE: wraps its body in ``try/except Exception``, logs
  ``_log.warning("derivatives_db.<fn> failed: %s", exc)`` and returns the empty
  value (``None`` / ``[]``). A bookkeeping read must never take down a release
  sweep or a page load;
* clamps ``limit`` with ``max(1, min(limit, 500))``;
* appends ``AND brand_id = %s`` only when ``brand_id`` is truthy.

Rows are plain ``dict[str, Any]`` (psycopg ``dict_row``); timestamps come back
as timezone-aware ``datetime``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from lib import db
from lib.derivatives_db.statuses import ACTIVE_STATUSES, FAILED_VISIBLE_HOURS, SLOT_HOLDING

_log = logging.getLogger(__name__)


def get(derivative_id: str) -> dict[str, Any] | None:
    """One derivative row by id, or ``None`` (missing row OR database error).

    SQL: ``SELECT * FROM content_derivatives WHERE id = %s``.
    """
    try:
        return db.fetch_one("SELECT * FROM content_derivatives WHERE id = %s", (derivative_id,))
    except Exception as exc:
        _log.warning("derivatives_db.get failed: %s", exc)
        return None


def list_for_review(
    *, brand_id: str | None = None, status: str = "queued", limit: int = 100
) -> list[dict[str, Any]]:
    """Rows for the review page, newest first, each with its source post's
    ``topic`` and ``wp_url`` joined in.

    ``status='queued'`` is the review listing and deliberately returns MORE than
    queued rows:

    * ``'composing'`` rows -- the in-flight signal. The page polls this listing
      every 5 s while any row is composing; there is no ``/status`` route, so a
      row that vanished while composing would make a just-created spotlight
      look like it was lost.
    * ``'fb_publishing'`` / ``'ig_publishing'`` rows -- a publish claim that was
      never released. A worker killed between ``publish.claim_publish`` and its
      result write (the 600 s subprocess timeout in ``lib.social_release``)
      leaves one behind, and NOTHING reaps them. Hidden, the spotlight just
      vanishes from the page with nothing published, which is a silent loss
      rather than the deliberate ``wedged`` stop -- there, a post IS live.
      Listing them is all this does: they are NEVER auto-released or
      auto-retried here, because a claim may belong to a post that did go out
      and republishing it is worse than showing a stuck card. The page renders
      them read-only; clearing one is a human decision.
    * ``'failed'`` rows whose ``updated_at`` is younger than
      ``statuses.FAILED_VISIBLE_HOURS`` -- so the owner sees WHY it failed
      (``error``) instead of nothing at all.

    Any other ``status`` returns exactly that status. Validation of ``status``
    against ``STATUSES`` is the API's job, not this function's.

    SQL (``%s`` order: status, status, FAILED_VISIBLE_HOURS, [brand_id], limit)::

        SELECT d.*, i.topic AS topic, i.wp_url AS wp_url
        FROM content_derivatives d
        JOIN content_ideas i ON i.id = d.idea_id
        WHERE (d.status = %s
               OR (%s = 'queued'
                   AND (d.status IN ('composing', 'fb_publishing', 'ig_publishing')
                        OR (d.status = 'failed'
                            AND d.updated_at > NOW() - make_interval(hours => %s)))))
          [AND d.brand_id = %s]
        ORDER BY d.created_at DESC
        LIMIT %s

    (``make_interval(hours => …)`` takes an INTEGER. That is fine for the whole
    number of hours here; it is why ``publish.set_fb_result``, whose gap is a
    float, uses ``secs`` instead.)
    """
    try:
        # `status` is passed TWICE on purpose. The second occurrence drives the
        # `%s = 'queued'` switch, which keeps the widening (composing, the two
        # unreaped publish claims, recently failed) out of every other listing
        # without building two query strings.
        params: list[Any] = [status, status, FAILED_VISIBLE_HOURS]
        clause = ""
        if brand_id:
            clause = " AND d.brand_id = %s"
            params.append(brand_id)
        params.append(max(1, min(limit, 500)))
        return db.fetch_all(
            "SELECT d.*, i.topic AS topic, i.wp_url AS wp_url "
            "FROM content_derivatives d "
            "JOIN content_ideas i ON i.id = d.idea_id "
            "WHERE (d.status = %s "
            "OR (%s = 'queued' "
            "AND (d.status IN ('composing', 'fb_publishing', 'ig_publishing') "
            "OR (d.status = 'failed' "
            "AND d.updated_at > NOW() - make_interval(hours => %s)))))"
            f"{clause} ORDER BY d.created_at DESC LIMIT %s",
            tuple(params),
        )
    except Exception as exc:
        _log.warning("derivatives_db.list_for_review failed: %s", exc)
        return []


def find_active(idea_id: str, product_key: str, fmt: str) -> dict[str, Any] | None:
    """The row that makes a new (idea, product, format) spotlight a duplicate.

    Called by the create route AFTER ``writes.insert_composing`` returned
    ``None``, to tell "duplicate" (row found -> 409 carrying its id) from
    "insert failed" (``None`` -> 500). The third parameter is ``fmt`` because
    ``format`` is a builtin; call it positionally.

    SQL: ``SELECT * FROM content_derivatives WHERE idea_id = %s AND
    product_key = %s AND format = %s AND status = ANY(%s) ORDER BY created_at
    DESC LIMIT 1`` with ``list(statuses.ACTIVE_STATUSES)``.
    """
    try:
        return db.fetch_one(
            "SELECT * FROM content_derivatives "
            "WHERE idea_id = %s AND product_key = %s AND format = %s AND status = ANY(%s) "
            "ORDER BY created_at DESC LIMIT 1",
            (idea_id, product_key, fmt, list(ACTIVE_STATUSES)),
        )
    except Exception as exc:
        _log.warning("derivatives_db.find_active failed: %s", exc)
        return None


def list_due_for_fb(*, brand_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Scheduled rows whose Facebook slot has arrived (database clock).

    SQL: ``SELECT * FROM content_derivatives WHERE status = 'scheduled' AND
    fb_due_at IS NOT NULL AND fb_due_at <= NOW() [AND brand_id = %s] ORDER BY
    fb_due_at ASC LIMIT %s``.

    A row listed here is a CANDIDATE, not a licence to publish: the worker must
    still win ``publish.claim_publish``.
    """
    try:
        params: list[Any] = []
        clause = ""
        if brand_id:
            clause = " AND brand_id = %s"
            params.append(brand_id)
        params.append(max(1, min(limit, 500)))
        return db.fetch_all(
            "SELECT * FROM content_derivatives "
            "WHERE status = 'scheduled' "
            "AND fb_due_at IS NOT NULL AND fb_due_at <= NOW()"
            f"{clause} ORDER BY fb_due_at ASC LIMIT %s",
            tuple(params),
        )
    except Exception as exc:
        _log.warning("derivatives_db.list_due_for_fb failed: %s", exc)
        return []


def list_due_for_ig(*, brand_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Rows whose FB half is live and whose FB<->IG gap has elapsed.

    SQL: ``SELECT * FROM content_derivatives WHERE status = 'fb_published' AND
    ig_due_at IS NOT NULL AND ig_due_at <= NOW() [AND brand_id = %s] ORDER BY
    ig_due_at ASC LIMIT %s``.
    """
    try:
        params: list[Any] = []
        clause = ""
        if brand_id:
            clause = " AND brand_id = %s"
            params.append(brand_id)
        params.append(max(1, min(limit, 500)))
        return db.fetch_all(
            "SELECT * FROM content_derivatives "
            "WHERE status = 'fb_published' "
            "AND ig_due_at IS NOT NULL AND ig_due_at <= NOW()"
            f"{clause} ORDER BY ig_due_at ASC LIMIT %s",
            tuple(params),
        )
    except Exception as exc:
        _log.warning("derivatives_db.list_due_for_ig failed: %s", exc)
        return []


def last_scheduled_fb_slot(*, brand_id: str | None = None) -> datetime | None:
    """The latest Facebook slot any derivative of this brand has claimed.

    One half of the shared calendar: ``lib.social_slot_allocator`` takes the
    max of this and ``lib.social_post_db.last_scheduled_fb_slot`` so regular
    posts and spotlights never land in the same hour.

    SQL: ``SELECT MAX(fb_due_at) AS slot FROM content_derivatives WHERE status
    = ANY(%s) [AND brand_id = %s]`` with ``list(statuses.SLOT_HOLDING)``;
    return ``row.get("slot") if row else None``.
    """
    try:
        params: list[Any] = [list(SLOT_HOLDING)]
        clause = ""
        if brand_id:
            clause = " AND brand_id = %s"
            params.append(brand_id)
        row = db.fetch_one(
            f"SELECT MAX(fb_due_at) AS slot FROM content_derivatives WHERE status = ANY(%s){clause}",
            tuple(params),
        )
        slot: datetime | None = row.get("slot") if row else None
        return slot
    except Exception as exc:
        _log.warning("derivatives_db.last_scheduled_fb_slot failed: %s", exc)
        return None
