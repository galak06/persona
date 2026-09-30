"""Create a product spotlight from the review page: pick a post, pick a product.

  GET  /api/v1/social-derivatives/sources                          — posts to pick from
  GET  /api/v1/social-derivatives/sources/{idea_id}/products       — the product picker
  POST /api/v1/social-derivatives                                   — create + dispatch compose

CONTRACT FROZEN BY PHASE 0 -- every path, method, status code, query parameter,
``response_model`` and documented error below is final, and so are the
module-level constants (tests assert the dispatched script / args / timeout
against them). Phase 1I replaces each ``HTTPException(501)`` body and adds
private helpers; it changes no decorator and no signature.

Mounted by ``api/social_derivatives_api.py`` (``router.include_router``), NOT
by ``api/approval_api.py`` directly, so the app registers one spotlight router.
That is also why this ``APIRouter`` declares no tags: it inherits
``social-posts`` from its parent instead of stacking a third copy.

Dispatch is a copy of ``api/social_posts_retry_api.py``, for the same reason:
the API image has no LLM/image credentials and no font stack, so the run goes
onto the shared ``flow-run`` queue and ``scripts/task_worker.py`` executes it
with the brand's own environment.

**This route cannot publish, structurally rather than by filtering.** It
dispatches ``scripts/social_derivative_compose.py``, which imports no
publisher, no worker and no release sweep, and it creates rows in
``'composing'`` -- a state the release sweep never selects. Only a human
approval (``POST /social-derivatives/{id}/approve``) can make a row eligible.

In-flight progress is the row itself (``status == 'composing'``); the page
polls the list. There is deliberately no ``/status`` route to drift from it.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from api import social_derivatives_support as support
from api.brand_context import resolve_api_brand
from api.social_derivatives_schemas import (
    CreateSpotlightRequest,
    CreateSpotlightResponse,
    ProductScope,
    SpotlightProductsResponse,
    SpotlightSourcesResponse,
)
from lib import derivatives_db, worker_db
from lib.crew.products.pool import load_candidate_pool
from lib.crew.reference_library import slugify
from lib.crew.spotlight.post_products import post_slug, products_in_post
from lib.derivatives_db import NewDerivative

# One writer call, one image generation, one overlay pass -- the same budget as
# a hook-image retry. Below `derivatives_db.STALE_COMPOSING_SECONDS` (1200) on
# purpose: a run must be killed by ITS timeout before the stale sweep may fail
# its row from under it.
_COMPOSE_TIMEOUT_SECONDS = 900
_COMPOSE_SCRIPT = "scripts/social_derivative_compose.py"
_FLOW_ID = "social-derivative-compose"

#: `worker_runs.status` meaning queued/running -- and never self-expiring.
_IN_FLIGHT = frozenset({"queued", "running"})

#: `content_ideas.status` values whose post is live on WordPress (the same pair
#: `lib.social_post_db` treats as postable).
_ELIGIBLE_IDEA_STATUSES = ("wp_published", "social_done")

log = logging.getLogger(__name__)

router = APIRouter()

# Makes check-then-enqueue atomic within this process; compose is single-flight
# PER BRAND (one worker slot, ~1 min per spotlight). The cross-request guard
# against composing the same spotlight twice is the partial unique index, not
# this lock.
_dispatch_lock = threading.Lock()


def _label(brand_id: str) -> str:
    """``worker_runs.worker_label`` for this brand's compose runs:
    ``"{brand_id}-social-derivative-compose"``. One label per brand, not per
    spotlight -- ``scripts/task_worker.py`` derives a log filename from it."""
    return f"{brand_id}-{_FLOW_ID}"


@router.get(
    "/social-derivatives/sources",
    response_model=SpotlightSourcesResponse,
    responses={404: {"description": "brand is not registered"}},
)
def list_spotlight_sources() -> SpotlightSourcesResponse:
    """Published posts a spotlight can be made from, newest first.

    ``resolve_api_brand()`` -> ``ideas_db.list_ideas(status=s, brand_id=...)``
    for each ``_ELIGIBLE_IDEA_STATUSES`` -> keep rows that have BOTH
    ``wp_post_id`` and ``wp_url`` and pass
    ``is_in_focus(row["category"], ContentStrategy(focus_category=
    brands_db.focus_category(brand_id)))`` (the ``ideas_db.py:147`` pattern).
    An idea whose own social post is already published is still a valid source
    -- that is the point of the feature.

    Errors: 404 / 500 from ``resolve_api_brand``.
    """
    brand_id, _brand_dir = resolve_api_brand()
    return SpotlightSourcesResponse(
        posts=support.published_sources(brand_id, _ELIGIBLE_IDEA_STATUSES)
    )


@router.get(
    "/social-derivatives/sources/{idea_id}/products",
    response_model=SpotlightProductsResponse,
    responses={
        404: {"description": "idea not found"},
        409: {"description": "idea has no published WordPress post"},
    },
)
def list_spotlight_products(
    idea_id: str,
    scope: Annotated[
        ProductScope,
        Query(description="'category' = the post's category; 'all' = the whole catalog"),
    ] = "category",
) -> SpotlightProductsResponse:
    """The product picker for one post: the merged catalog, marked up.

    Source is the WHOLE ``load_candidate_pool(Path(brand_dir))``.
    ``scope="category"`` keeps entries whose ``slugify(entry.category or "")``
    equals ``slugify(idea["category"])`` PLUS every in-post product whatever
    its category; ``scope="all"`` keeps everything, and so does
    ``scope="category"`` when that slug is ``""`` (an uncategorised idea of a
    brand with no focus -- filtering on "" would hide the whole catalog).
    ``slugify`` is ``lib.crew.reference_library.slugify`` on BOTH sides: the
    idea says "Dental Care", the catalog says "dental-care". Order: in-post
    products first (post link order), then the rest by ``display``
    (case-insensitive).

    WordPress is best-effort. ``wp_source.fetch_post(str(idea["wp_post_id"]))``
    returning ``None`` OR raising ``lib.errors.ConfigurationError`` (WP_* env
    missing in this container), ``httpx.HTTPError`` or ``ValueError`` (non-JSON
    body) => ``post_scan="unavailable"``, every ``in_post=False``,
    ``unknown_asins=[]``, slug from ``post_slug(None, wp_url=..., fallback_title=
    topic)`` -- a 200, never an error, so the list still works. Otherwise
    ``post_scan="ok"`` and ``products_in_post(post["content"]["rendered"], pool)``.

    Logs ``social_derivative_products_listed``.

    Errors: 404 idea not found (or another brand's); 409 idea has no
    ``wp_post_id``/``wp_url``; 422 idea out of focus; 422 bad ``scope``
    (FastAPI, from the ``Literal``).
    """
    brand_id, brand_dir = resolve_api_brand()
    idea, strategy = support.spotlight_idea(idea_id, brand_id)
    topic = str(idea.get("topic") or "")
    wp_url = str(idea.get("wp_url") or "")

    pool = load_candidate_pool(Path(brand_dir))
    post_scan, post = support.scan_post(idea, log)
    in_post, unknown_asins = (
        products_in_post(support.rendered_html(post), pool) if post is not None else ([], [])
    )

    # The idea's own category wins; the brand focus is the fallback so a post
    # WordPress never categorised still narrows to what this brand sells.
    category = slugify(str(idea.get("category") or "") or strategy.focus_category)
    products = support.ordered_products(pool, in_post, scope=scope, category=category)
    support.log_event(
        log,
        "social_derivative_products_listed",
        brand=brand_id,
        idea_id=idea_id,
        scope=scope,
        post_scan=post_scan,
        products=len(products),
        in_post=len(in_post),
        unknown_asins=len(unknown_asins),
    )
    return SpotlightProductsResponse(
        idea_id=idea_id,
        slug=post_slug(post, wp_url=wp_url, fallback_title=topic),
        scope=scope,
        category=category,
        post_scan=post_scan,
        products=products,
        unknown_asins=unknown_asins,
    )


@router.post(
    "/social-derivatives",
    status_code=202,
    response_model=CreateSpotlightResponse,
    responses={
        404: {"description": "idea not found"},
        409: {
            "description": "duplicate active spotlight (detail.existing_id carries its id), "
            "a compose run already in flight for this brand, or no WordPress post"
        },
        503: {"description": "compose dispatch failed; the row was marked 'failed' first"},
    },
)
def create_spotlight(body: CreateSpotlightRequest) -> CreateSpotlightResponse:
    """Create one spotlight in ``'composing'`` and dispatch its compose run.
    202 immediately; the page polls ``GET /social-derivatives``.

    Order (each step's failure stops the rest):

    1. ``resolve_api_brand()``.
    2. ``derivatives_db.fail_stale_composing(brand_id=brand_id)`` -- a killed
       run must not block re-creating its spotlight, and step 6 leans on it.
    3. Load the idea (404; 409 without ``wp_post_id``/``wp_url``) and apply the
       focus gate (422).
    4. ``load_candidate_pool(Path(brand_dir)).get(body.product_key)`` -> 422
       when absent. A product that is NOT in the post is ALLOWED.
    5. ``reference_category``: ``slugify`` + must be a key of
       ``existing_images_by_category(Path(brand_dir))`` -> 422 otherwise
       (``social_posts_retry_api._validated_category`` semantics; ``""`` passes).
    6. Under ``_dispatch_lock``: 409 when ``worker_db.get_one(brand_dir,
       _label(brand_id), brand_id)`` is ``_IN_FLIGHT`` AND a 'composing' row
       survived step 2; then ``derivatives_db.insert_composing(NewDerivative(...))``.
       ``None`` -> ``derivatives_db.find_active(idea_id, product_key, format)``:
       found -> 409 with ``detail={"message": ..., "existing_id": <id>}``; not
       found -> 503. Then ``flow_queue.dispatch(schedule_task_id=_label(brand_id),
       script=_COMPOSE_SCRIPT, args=["--derivative-id", new_id], brand=brand_id,
       brand_dir=brand_dir, timeout_seconds=_COMPOSE_TIMEOUT_SECONDS)``; if it
       raises -> ``derivatives_db.mark_failed(new_id, error="dispatch_failed")``
       FIRST, log ``social_derivative_dispatch_failed``, then 503. Finally
       ``worker_db.record_queued(brand_dir, _label(brand_id), brand_id)``.

    The row is inserted BEFORE the push (the reverse of the retry route)
    because the script needs the row's id as its argument; ``mark_failed`` on a
    failed push is what keeps that ordering from leaving a wedged row.

    Logs ``social_derivative_created`` then
    ``social_derivative_compose_dispatched``. Returns ``{id, status:
    "composing"}``.
    """
    brand_id, brand_dir = resolve_api_brand()
    # A killed run leaves a 'composing' row in the partial unique index and a
    # `worker_runs` row nothing completes; sweeping it expires both guards below.
    derivatives_db.fail_stale_composing(brand_id=brand_id)
    support.spotlight_idea(body.idea_id, brand_id)

    entry = load_candidate_pool(Path(brand_dir)).get(body.product_key)
    if entry is None:
        raise HTTPException(
            status_code=422, detail=f"'{body.product_key}' is not in this brand's product catalog"
        )
    category = support.validated_category(brand_dir, body.reference_category)
    label = _label(brand_id)

    with _dispatch_lock:
        composing = derivatives_db.list_for_review(brand_id=brand_id, status="composing", limit=1)
        current = worker_db.get_one(brand_dir, label, brand_id) or {}
        if composing and str(current.get("status")) in _IN_FLIGHT:
            raise HTTPException(status_code=409, detail="a spotlight is already being composed")
        new_id = support.create_composing(
            NewDerivative(
                idea_id=body.idea_id,
                brand_id=brand_id,
                product_key=entry.key,
                product_asin=entry.asin,
                product_display=entry.display,
                format=body.format,
                reference_category=category,
            )
        )
        support.log_event(
            log,
            "social_derivative_created",
            brand=brand_id,
            derivative_id=new_id,
            idea_id=body.idea_id,
            product_key=entry.key,
            reference_category=category,
        )
        support.dispatch_compose(
            new_id,
            log=log,
            label=label,
            script=_COMPOSE_SCRIPT,
            brand_id=brand_id,
            brand_dir=brand_dir,
            timeout_seconds=_COMPOSE_TIMEOUT_SECONDS,
        )
        worker_db.record_queued(brand_dir, label, brand_id)

    support.log_event(
        log,
        "social_derivative_compose_dispatched",
        brand=brand_id,
        derivative_id=new_id,
        script=_COMPOSE_SCRIPT,
        timeout_seconds=_COMPOSE_TIMEOUT_SECONDS,
    )
    return CreateSpotlightResponse(id=new_id, status="composing")
