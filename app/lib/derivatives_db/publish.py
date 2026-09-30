"""The publish half of the ``content_derivatives`` state machine.

CONTRACT FROZEN BY PHASE 0 -- signatures, return types and the SQL guard each
docstring spells out are final. Phase 1A replaces the ``NotImplementedError``
bodies (and drops the ``# noqa: F401`` on the ``db`` import) and changes
nothing else.

Why a CLAIM at all. The regular social-post worker reads the row, checks
``status == 'scheduled'``, publishes, then writes the result. Two overlapping
release sweeps both pass that read and both publish. Here the order is
inverted: the worker must WIN a conditional UPDATE into ``'*_publishing'``
before it touches the Graph API, so exactly one process can ever hold a row.

What happens after the claim, and why the three outcomes differ:

* publisher RAISED -> nothing went out -> ``release_publish_claim`` puts the
  row back so the next sweep retries it.
* publisher returned, result write succeeded -> normal forward transition.
* publisher returned, result write FAILED -> the row STAYS in
  ``'*_publishing'``. It is error-logged (``social_derivative_result_write_failed``)
  and never auto-reposted: nothing selects ``'*_publishing'`` rows, so a live
  post can never be published twice. A wedged row costs one manual UPDATE; a
  duplicate post on the brand's page cannot be taken back.

Same defensive style as ``writes.py``: ``db.execute``, ``rowcount > 0``,
``try/except Exception`` -> ``_log.warning("derivatives_db.<fn> failed: %s",
exc)`` -> ``False``. Never raises -- including on an unknown ``platform``,
which logs a warning and returns ``False``.
"""

from __future__ import annotations

import logging
from typing import Literal

from lib import db

_log = logging.getLogger(__name__)

#: Which half of the post. The worker's ``--platform`` values.
Platform = Literal["fb", "ig"]


def claim_publish(derivative_id: str, platform: Platform) -> bool:
    """Atomically take the right to publish one half. ``True`` if THIS call won.

    The due check is INSIDE the same UPDATE (database clock), so a row whose
    slot was moved or released between the sweep's SELECT and this call is
    refused rather than published early.

    ``platform='fb'``::

        UPDATE content_derivatives SET status = 'fb_publishing', updated_at = NOW()
        WHERE id = %s AND status = 'scheduled'
          AND fb_due_at IS NOT NULL AND fb_due_at <= NOW()

    ``platform='ig'``::

        UPDATE content_derivatives SET status = 'ig_publishing', updated_at = NOW()
        WHERE id = %s AND status = 'fb_published'
          AND ig_due_at IS NOT NULL AND ig_due_at <= NOW()
    """
    try:
        if platform == "fb":
            query = (
                "UPDATE content_derivatives SET status = 'fb_publishing', updated_at = NOW() "
                "WHERE id = %s AND status = 'scheduled' "
                "AND fb_due_at IS NOT NULL AND fb_due_at <= NOW()"
            )
        elif platform == "ig":
            query = (
                "UPDATE content_derivatives SET status = 'ig_publishing', updated_at = NOW() "
                "WHERE id = %s AND status = 'fb_published' "
                "AND ig_due_at IS NOT NULL AND ig_due_at <= NOW()"
            )
        else:
            _log.warning("derivatives_db.claim_publish failed: unknown platform %r", platform)
            return False
        return db.execute(query, (derivative_id,)) > 0
    except Exception as exc:
        _log.warning("derivatives_db.claim_publish failed: %s", exc)
        return False


def release_publish_claim(derivative_id: str, platform: Platform) -> bool:
    """Give a claim back. Call ONLY when the publisher raised.

    ``'fb'``: ``'fb_publishing' -> 'scheduled'`` (``fb_due_at`` untouched, so
    the row is due again on the next sweep). ``'ig'``: ``'ig_publishing' ->
    'fb_published'`` (``ig_due_at`` untouched). Each guarded on its
    ``'*_publishing'`` state. Never call this after a publisher RETURNED --
    see the module docstring.
    """
    try:
        if platform == "fb":
            query = (
                "UPDATE content_derivatives SET status = 'scheduled', updated_at = NOW() "
                "WHERE id = %s AND status = 'fb_publishing'"
            )
        elif platform == "ig":
            query = (
                "UPDATE content_derivatives SET status = 'fb_published', updated_at = NOW() "
                "WHERE id = %s AND status = 'ig_publishing'"
            )
        else:
            _log.warning(
                "derivatives_db.release_publish_claim failed: unknown platform %r", platform
            )
            return False
        return db.execute(query, (derivative_id,)) > 0
    except Exception as exc:
        _log.warning("derivatives_db.release_publish_claim failed: %s", exc)
        return False


def set_fb_result(derivative_id: str, *, url: str | None, ig_gap_hours: float) -> bool:
    """Record the live FB Page post and arm the IG half:
    ``'fb_publishing' -> 'fb_published'``.

    ``ig_due_at`` is stamped by the DATABASE, relative to the moment the FB
    post actually went out -- same reasoning as
    ``lib.social_post_db.set_fb_result``. ``make_interval(hours => …)`` only
    accepts an integer and the gap is a float, hence seconds::

        UPDATE content_derivatives
        SET status = 'fb_published', fb_page_post_url = %s,
            ig_due_at = NOW() + make_interval(secs => %s), updated_at = NOW()
        WHERE id = %s AND status = 'fb_publishing'

    with ``float(ig_gap_hours) * 3600.0``. Guarded on the claim, so a replayed
    call cannot push an already-armed IG half further into the future.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_derivatives SET status = 'fb_published', "
            "fb_page_post_url = %s, "
            "ig_due_at = NOW() + make_interval(secs => %s), "
            "updated_at = NOW() "
            "WHERE id = %s AND status = 'fb_publishing'",
            (url, float(ig_gap_hours) * 3600.0, derivative_id),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("derivatives_db.set_fb_result failed: %s", exc)
        return False


def set_ig_result(derivative_id: str, *, url: str | None) -> bool:
    """Record the live IG feed post: ``'ig_publishing' -> 'published'``.

    Sets ``ig_post_url = %s``. Guard: ``WHERE id = %s AND status =
    'ig_publishing'``.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_derivatives SET status = 'published', "
            "ig_post_url = %s, updated_at = NOW() "
            "WHERE id = %s AND status = 'ig_publishing'",
            (url, derivative_id),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("derivatives_db.set_ig_result failed: %s", exc)
        return False
