"""Regenerate a queued social post's hook image, from the review page.

  POST /api/v1/social-posts/{id}/retry-image         — dispatch one retry
  GET  /api/v1/social-posts/{id}/retry-image/status  — is it running / how did it end

A post whose image fell back to the WP hero (`social_post_source='fallback'`)
had exactly two moves on the review page, and both lose something: approve and
ship a stock photo, or reject and throw away captions that are usually fine.
This is the third — keep the post, replace the image. `lib.crew.socialpost.retry`
explains why that cannot be "run the same generation again" and what makes a
retry reach a different photo.

Dispatch matches `api/social_posts_compose_api.py`: the API image has no
LLM/image credentials and no font stack, so the run is pushed onto the shared
`flow-run` Redis queue and executed by `scripts/task_worker.py` with the
brand's own environment.

**This route cannot publish, structurally rather than by filtering.** It
dispatches `scripts/social_post_retry_image.py`, a script that imports no
publisher and no release sweep — unlike the compose button, which dispatches
the pipeline that DOES publish and therefore has to strip `--release-only` and
force `--compose-only` from the row's args to stay safe. There are no args here
to get wrong: the idea id is the one being retried, and the category is
validated against the brand's own stocked reference-library tags before it is
ever put on the queue.

Progress is read off the idea row itself. `retry_hook_image` claims it
`'queued' -> 'composing'` for the duration, so `'composing'` IS the per-post
in-flight signal — no second source of truth to drift, correct across a page
reload or another tab, and it is the same claim that stops the post being
approved or rejected mid-retry.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.brand_context import resolve_api_brand
from lib import flow_queue, ideas_db, worker_db
from lib.crew.reference_library import existing_images_by_category, slugify

# One writer call, one image generation, one overlay pass. Generous next to the
# ~60s a live run takes, tight enough that a wedged run frees the queue slot.
_RETRY_TIMEOUT_SECONDS = 900
_RETRY_SCRIPT = "scripts/social_post_retry_image.py"
_FLOW_ID = "social-post-retry-image"

_COMPOSING = "composing"
_QUEUED = "queued"
_IN_FLIGHT = frozenset({"queued", "running"})

log = logging.getLogger(__name__)

router = APIRouter(tags=["social-posts"])

# Makes check-then-enqueue atomic within this process. The real cross-process
# guard is the DB claim inside the run itself (`claim_for_recompose`), which is
# why a lost race here costs nothing: the second run finds the row already
# 'composing' and stops without touching it.
_dispatch_lock = threading.Lock()


class RetryImageRequest(BaseModel):
    """Which reference collection to anchor the new image on.

    Empty means "decide for me": the fresh plan's own category if the library
    can match it, otherwise any photo of the brand's mascot. A slug the brand
    has no photos under is rejected rather than silently ignored — it would
    resolve to nothing and the retry would reproduce the fallback it was
    clicked to escape.
    """

    reference_category: str = ""


class RetryImageStatus(BaseModel):
    # True while the row is claimed for regeneration. Read from the post, not
    # from the worker, so it names the RIGHT post rather than the brand's most
    # recent run.
    running: bool
    # The post's `social_post_status`, so a caller can tell "back in the queue"
    # from "approved while I was away".
    status: str
    # `social_post_source` as it stands now — 'gemini' once a retry has landed
    # a generated image, still 'fallback' if the run failed and changed nothing.
    source: str | None = None
    # Outcome of this brand's last retry run. None while one is in flight.
    ok: bool | None = None
    detail: str | None = None


def _label(brand_id: str) -> str:
    """`worker_runs.worker_label` for this brand's retry runs.

    One label per brand rather than per post: `scripts/task_worker.py` derives
    a log filename from it (`logs/cron_social_post_retry_image.log`), and a
    per-post label would scatter one file per idea id through the brand's log
    directory. Which post a run belonged to is on the row, not the label.
    """
    return f"{brand_id}-{_FLOW_ID}"


def _validated_category(brand_dir: str, requested: str) -> str:
    """The requested slug, proven to hold at least one photo. 422 otherwise.

    `with_photos` is the whole point: a declared-but-empty tag reads as a real
    choice in the picker and resolves to no image at all.
    """
    slug = slugify(requested)
    if not slug:
        return ""
    stocked = existing_images_by_category(Path(brand_dir))
    if slug not in stocked:
        raise HTTPException(
            status_code=422,
            detail=f"'{requested}' holds no reference photos. Stocked: {sorted(stocked)}",
        )
    return slug


def _queued_idea(idea_id: str) -> dict[str, object]:
    idea = ideas_db.get_idea(idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="idea not found")
    status = str(idea.get("social_post_status") or "")
    if status != _QUEUED:
        detail = (
            "a retry is already regenerating this post's image"
            if status == _COMPOSING
            else f"idea is '{status}', not 'queued'"
        )
        raise HTTPException(status_code=409, detail=detail)
    return idea


@router.post("/social-posts/{idea_id}/retry-image", status_code=202)
def retry_social_post_image(idea_id: str, body: RetryImageRequest | None = None) -> dict[str, str]:
    """Dispatch one image regeneration to the worker. 202 immediately; poll
    /social-posts/{id}/retry-image/status.

    Deliberately not restricted to `source='fallback'` rows. What must be true
    is that the post is still QUEUED — that is the state the whole operation is
    guarded on. Which queued posts are worth the credits is a judgement the
    review page makes (it offers this on posts with no generated image), and
    baking that policy in here too would mean two places to change it.
    """
    brand_id, brand_dir = resolve_api_brand()
    _queued_idea(idea_id)
    category = _validated_category(brand_dir, (body or RetryImageRequest()).reference_category)
    label = _label(brand_id)
    args = ["--idea-id", idea_id]
    if category:
        args += ["--reference-category", category]

    with _dispatch_lock:
        current = worker_db.get_one(brand_dir, label, brand_id)
        if current is not None and str(current.get("status")) in _IN_FLIGHT:
            raise HTTPException(status_code=409, detail="a retry is already in progress")

        # Pushed BEFORE the row is claimed: a Redis failure then leaves nothing
        # marked in flight, rather than a 'queued' row that blocks every future
        # retry for this brand until someone clears it by hand. The window this
        # opens (two clicks both enqueuing) is closed downstream by
        # `claim_for_recompose`, which only one run can win.
        flow_queue.dispatch(
            schedule_task_id=label,
            script=_RETRY_SCRIPT,
            args=args,
            brand=brand_id,
            brand_dir=brand_dir,
            timeout_seconds=_RETRY_TIMEOUT_SECONDS,
        )
        worker_db.record_queued(brand_dir, label, brand_id)

    log.info(
        json.dumps(
            {
                "event": "social_post_retry_dispatched",
                "brand": brand_id,
                "idea_id": idea_id,
                "reference_category": category,
            }
        )
    )
    return {"status": "started", "id": idea_id}


@router.get("/social-posts/{idea_id}/retry-image/status", response_model=RetryImageStatus)
def retry_image_status(idea_id: str) -> RetryImageStatus:
    """Is this post's image being regenerated, and how did the last run end?"""
    brand_id, brand_dir = resolve_api_brand()
    idea = ideas_db.get_idea(idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="idea not found")

    status = str(idea.get("social_post_status") or "")
    source = idea.get("social_post_source")
    if status == _COMPOSING:
        return RetryImageStatus(running=True, status=status, source=str(source) if source else None)

    row = worker_db.get_one(brand_dir, _label(brand_id), brand_id)
    if row is None:
        return RetryImageStatus(
            running=False, status=status, source=str(source) if source else None
        )
    run_status = str(row.get("status") or "")
    message = str(row.get("message") or "")
    return RetryImageStatus(
        running=False,
        status=status,
        source=str(source) if source else None,
        # A run still `queued`/`running` on the worker while this post is NOT
        # 'composing' is someone else's retry, so it says nothing about this
        # post's outcome.
        ok=None if run_status in _IN_FLIGHT else run_status == "success",
        detail=message[-500:] or None,
    )
