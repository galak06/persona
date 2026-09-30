"""Product-spotlight review API — the ``content_derivatives`` track.

  GET   /api/v1/social-derivatives                   — review / scheduled list
  GET   /api/v1/social-derivatives/{id}/image        — the composed hook image
  POST  /api/v1/social-derivatives/{id}/approve      — claim the next SHARED FB slot
  POST  /api/v1/social-derivatives/{id}/unschedule   — release the slot, re-queue
  POST  /api/v1/social-derivatives/{id}/reject       — terminal reject + cleanup

plus everything in ``api/social_derivatives_create_api.py`` (sources, product
picker, create), which this router mounts so ``api/approval_api.py`` registers
exactly one spotlight router.

CONTRACT FROZEN BY PHASE 0 -- every path, method, status code, query parameter,
``response_model`` and documented error below is final. Phase 1I replaces each
``HTTPException(501)`` body and adds private helpers; it changes no decorator
and no signature.

A deliberate sibling of ``api/social_posts_api.py`` rather than more routes on
it: that module reads ``content_ideas`` columns, this one reads another table
with a longer lifecycle, and folding them together would put two state
machines behind one ``{id}`` that means a different thing in each.

Approve SCHEDULES; it does not publish -- same as regular posts, and into the
SAME calendar: ``lib.social_slot_allocator.next_shared_fb_slot`` takes the
latest slot across BOTH tables, so a spotlight and a regular post can never be
handed the same hour. Publishing is the hourly release sweep
(``lib.social_release.release_due``) and
``recipe-publisher/workers/worker_social_derivative.py``.

Every call into the table goes through the package name
(``from lib import derivatives_db`` -> ``derivatives_db.<fn>``): the route
tests swap that one attribute for an in-memory fake.
"""

from __future__ import annotations

import logging
import os
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from api import social_derivatives_support as support
from api.brand_context import resolve_api_brand
from api.social_derivatives_create_api import router as _create_router
from api.social_derivatives_schemas import (
    SocialDerivative,
    SocialDerivativesResponse,
    SpotlightDecisionResponse,
)
from lib import derivatives_db, social_slot_allocator

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

log = logging.getLogger(__name__)

router = APIRouter(tags=["social-posts"])
router.include_router(_create_router)


def _brand_root() -> Path:
    """Where BRAND_DIR-relative ``image_path`` values resolve -- the
    ``api.social_posts_api._resolve_brand_path`` contract: composer (worker)
    and API (container) agree on the relative path, not the absolute mount."""
    brand = os.environ.get("BRAND_DIR")
    return Path(brand) if brand else _PROJECT_ROOT


def _image_file(row: dict[str, Any]) -> Path | None:
    """The row's composed image on disk, or ``None``.

    ``None`` covers all three "no image" cases with one answer -- never
    composed, path escapes the brand root, file deleted -- because the two
    callers do the same thing with each: the route 404s, reject skips the
    unlink.
    """
    target = support.resolve_under(_brand_root(), str(row.get("image_path") or ""))
    return target if target is not None and target.is_file() else None


def _row_or_404(derivative_id: str) -> dict[str, Any]:
    row = derivatives_db.get(derivative_id)
    if row is None:
        raise HTTPException(status_code=404, detail="spotlight not found")
    return row


@router.get(
    "/social-derivatives",
    response_model=SocialDerivativesResponse,
    responses={404: {"description": "brand is not registered"}},
)
def list_social_derivatives(
    status: str = Query("queued", description="content_derivatives.status filter"),
    limit: int = Query(100, ge=1, le=500),
) -> SocialDerivativesResponse:
    """Spotlights for the review page, ``'queued'`` by default.

    422 when ``status`` is not in ``derivatives_db.STATUSES`` (detail lists the
    valid values, like ``GET /social-posts``). Then ``resolve_api_brand()``,
    ``derivatives_db.fail_stale_composing(brand_id=brand_id)`` (so a dead run
    surfaces as 'failed' on the next poll instead of spinning forever), and
    ``derivatives_db.list_for_review(brand_id=brand_id, status=status,
    limit=limit)`` mapped with ``SocialDerivative.from_row``.

    ``status="queued"`` also carries 'composing' rows and recently 'failed'
    ones -- see ``list_for_review``. ``composing: true`` on any row is the
    page's cue to keep polling every 5 s.
    """
    if status not in derivatives_db.STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{status}'. Valid: {sorted(derivatives_db.STATUSES)}",
        )
    brand_id, _brand_dir = resolve_api_brand()
    derivatives_db.fail_stale_composing(brand_id=brand_id)
    rows = derivatives_db.list_for_review(brand_id=brand_id, status=status, limit=limit)
    derivatives = [SocialDerivative.from_row(row) for row in rows]
    return SocialDerivativesResponse(derivatives=derivatives, total=len(derivatives))


@router.get(
    "/social-derivatives/{derivative_id}/image",
    response_class=FileResponse,
    responses={404: {"description": "spotlight, image path or file not found"}},
)
def get_social_derivative_image(derivative_id: str) -> FileResponse:
    """Serve the composed hook image: ``image/jpeg``, ``Cache-Control: no-store``.

    The stored path is resolved under ``_brand_root()`` and must stay under it:
    ``target = (root / image_path).resolve()`` and
    ``target.is_relative_to(root.resolve())``, else 404 -- a row is
    worker-written data, and a ``../`` in it must not become a file read.

    Errors: 404 no row / no ``image_path`` / escapes the root / not a file.
    """
    target = _image_file(_row_or_404(derivative_id))
    if target is None:
        raise HTTPException(status_code=404, detail="spotlight image not available")
    return FileResponse(target, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post(
    "/social-derivatives/{derivative_id}/approve",
    response_model=SpotlightDecisionResponse,
    responses={
        404: {"description": "spotlight not found"},
        409: {"description": "spotlight is not 'queued'"},
    },
)
def approve_social_derivative(derivative_id: str) -> SpotlightDecisionResponse:
    """Approve a queued spotlight by claiming the next free SHARED FB slot.

    ``derivatives_db.get`` (404; 409 unless ``status == 'queued'``) ->
    ``due_at = social_slot_allocator.next_shared_fb_slot(datetime.now(UTC),
    brand_id=row["brand_id"])`` -> ``derivatives_db.schedule_fb(id,
    due_at=due_at)`` (``False`` -> 409 "no longer 'queued'"). Nothing
    publishes here. Logs ``social_derivative_scheduled``. Returns ``{id,
    status: "scheduled", fb_due_at: due_at.isoformat()}``.
    """
    row = _row_or_404(derivative_id)
    if row.get("status") != "queued":
        raise HTTPException(
            status_code=409, detail=f"spotlight is '{row.get('status')}', not 'queued'"
        )
    due_at = social_slot_allocator.next_shared_fb_slot(
        datetime.now(UTC), brand_id=row.get("brand_id")
    )
    if not derivatives_db.schedule_fb(derivative_id, due_at=due_at):
        raise HTTPException(status_code=409, detail="spotlight is no longer 'queued'")
    support.log_event(
        log,
        "social_derivative_scheduled",
        derivative_id=derivative_id,
        due_at=due_at.isoformat(),
    )
    return SpotlightDecisionResponse(
        id=derivative_id, status="scheduled", fb_due_at=due_at.isoformat()
    )


@router.post(
    "/social-derivatives/{derivative_id}/unschedule",
    response_model=SpotlightDecisionResponse,
    responses={409: {"description": "spotlight is not 'scheduled'"}},
)
def unschedule_social_derivative(derivative_id: str) -> SpotlightDecisionResponse:
    """Release a scheduled spotlight's slot and put it back in review.

    ``derivatives_db.unschedule_fb(id)`` -> ``False`` = 409 (not 'scheduled',
    which includes "the worker already claimed it"). Logs
    ``social_derivative_unscheduled``. Returns ``{id, status: "queued"}``.
    """
    if not derivatives_db.unschedule_fb(derivative_id):
        raise HTTPException(status_code=409, detail="spotlight is not 'scheduled'")
    support.log_event(log, "social_derivative_unscheduled", derivative_id=derivative_id)
    return SpotlightDecisionResponse(id=derivative_id, status="queued")


@router.post(
    "/social-derivatives/{derivative_id}/reject",
    response_model=SpotlightDecisionResponse,
    responses={
        404: {"description": "spotlight not found"},
        409: {"description": "spotlight is not 'queued'"},
    },
)
def reject_social_derivative(derivative_id: str) -> SpotlightDecisionResponse:
    """Reject a queued spotlight: terminal ``'rejected'`` + delete its image.

    ``derivatives_db.get`` (404) -> ``derivatives_db.reject(id)`` (``False`` ->
    409) -> unlink the image, resolved with the same ``is_relative_to`` guard
    as the image route (``missing_ok``). 'rejected' is outside the unique
    index, so the same spotlight can be created again afterwards. Logs
    ``social_derivative_rejected``. Returns ``{id, status: "rejected"}``.
    """
    row = _row_or_404(derivative_id)
    if not derivatives_db.reject(derivative_id):
        raise HTTPException(status_code=409, detail="spotlight is not 'queued'")
    # Best effort: the row is already terminal, and a file that cannot be
    # deleted (gone, read-only mount) must not turn a successful reject into a
    # 500 the operator would retry against an already-rejected row.
    target = _image_file(row)
    if target is not None:
        with suppress(OSError):
            target.unlink(missing_ok=True)
    support.log_event(log, "social_derivative_rejected", derivative_id=derivative_id)
    return SpotlightDecisionResponse(id=derivative_id, status="rejected")
