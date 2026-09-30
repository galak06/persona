"""Private helpers behind the two product-spotlight route modules.

``api/social_derivatives_create_api.py`` arrives from phase 0 already 197 lines
long, and every one of them is frozen contract: constants the tests assert the
dispatch against, decorators that ARE the OpenAPI surface, and docstrings that
are emitted verbatim into ``frontend/openapi.json``. There is no room in it for
the idea gate, the best-effort WordPress scan and the product ordering, and
copying those into ``api/social_derivatives_api.py`` would give the two halves
of one resource two subtly different answers to "is this idea usable?".

So they live here, on the far side of the 300-line limit, and BOTH route
modules call them. Nothing in this file is part of the API contract: it builds
no route, owns no path, and is free to change shape as long as the routes keep
answering the statuses their docstrings document.

The one thing that IS a contract here is the exception type: every guard raises
``HTTPException`` rather than returning a sentinel, so a route reads as the
happy path and a caller can never forget to check a return value on the way to
a 200 that should have been a 409.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException

from api.social_derivatives_schemas import PostScan, ProductScope, SpotlightProduct, SpotlightSource
from lib import brands_db, derivatives_db, flow_queue, ideas_db
from lib.affiliate_resolver import ProductEntry
from lib.content_strategy import ContentStrategy, is_in_focus
from lib.crew import wp_source
from lib.crew.reference_library import existing_images_by_category, slugify
from lib.derivatives_db import NewDerivative
from lib.errors import ConfigurationError

#: "WordPress did not answer" as a catchable shape. `wp_client()` raises
#: `ConfigurationError` when WP_URL/WP_USER/WP_APP_PASSWORD are missing -- which
#: is the NORMAL state of the `persona-api-1` container -- `httpx.HTTPError`
#: covers every transport failure, and `ValueError` is what `resp.json()` raises
#: when WordPress answers 200 with an HTML error page. All three mean the same
#: thing to the product picker: show the catalog, mark nothing as in-post.
WP_UNAVAILABLE = (ConfigurationError, httpx.HTTPError, ValueError)


def log_event(log: logging.Logger, event: str, **fields: Any) -> None:
    """One structured line, the `api/` way: stdlib logging + a JSON payload.

    Separate from `lib.observability.get_logger` on purpose -- the API's log
    lines are collected by uvicorn, and the crew helper's keyword-argument
    rendering would not survive that formatter intact.
    """
    log.info(json.dumps({"event": event, **fields}))


def brand_strategy(brand_id: str) -> ContentStrategy:
    """This brand's one-focus-category stance (``ideas_db.py:147`` pattern)."""
    return ContentStrategy(focus_category=brands_db.focus_category(brand_id))


def spotlight_idea(idea_id: str, brand_id: str) -> tuple[dict[str, Any], ContentStrategy]:
    """The source post for a spotlight, gated as both routes document it.

    Returns the idea row AND the strategy used to gate it, so the products
    route can reuse the brand's focus category as its filter fallback without
    a second `brands_db` round trip.

    404 unknown idea (or another brand's -- an id from someone else's page must
    not reveal their topic); 409 no live WordPress post, because the whole
    feature reads the published article; 422 out of focus, the same gate
    `ideas_db.insert_idea` applies to idea creation.
    """
    idea = ideas_db.get_idea(idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="idea not found")
    row_brand = str(idea.get("brand_id") or "")
    if row_brand and row_brand != brand_id:
        raise HTTPException(status_code=404, detail="idea not found")
    if not (idea.get("wp_post_id") and idea.get("wp_url")):
        raise HTTPException(status_code=409, detail="idea has no published WordPress post")
    strategy = brand_strategy(brand_id)
    if not is_in_focus(idea.get("category"), strategy):
        raise HTTPException(
            status_code=422,
            detail=(
                f"idea category {str(idea.get('category') or '')!r} is outside this brand's "
                f"focus category {strategy.focus_category!r}"
            ),
        )
    return idea, strategy


def published_sources(brand_id: str, statuses: Sequence[str]) -> list[SpotlightSource]:
    """Every live, in-focus post of this brand, newest first, deduplicated.

    An idea whose own social post already shipped is still a source -- making a
    second, product-led post out of an article that is already earning is the
    whole point -- so both published statuses are walked and the ids merged.
    """
    strategy = brand_strategy(brand_id)
    posts: list[SpotlightSource] = []
    seen: set[str] = set()
    for status in statuses:
        # `list_ideas` RAISES on a DB failure rather than degrading to `[]` (see
        # its docstring). An empty picker would read as "this brand has
        # published nothing", so the failure is reported as one.
        try:
            rows = ideas_db.list_ideas(status=status, brand_id=brand_id)
        except Exception as exc:  # any DB failure is the same 500 to the picker
            raise HTTPException(status_code=500, detail="could not read published posts") from exc
        for row in rows:
            idea_id = str(row.get("id") or "")
            wp_url = str(row.get("wp_url") or "")
            if not idea_id or idea_id in seen or not row.get("wp_post_id") or not wp_url:
                continue
            if not is_in_focus(row.get("category"), strategy):
                continue
            seen.add(idea_id)
            posts.append(
                SpotlightSource(
                    idea_id=idea_id,
                    topic=str(row.get("topic") or ""),
                    wp_url=wp_url,
                    category=str(row.get("category") or ""),
                )
            )
    return posts


def create_composing(new: NewDerivative) -> str:
    """The new row's id, or the 409/503 the create route documents.

    `insert_composing` answers ``None`` for BOTH "the partial unique index
    refused a duplicate" and "the write failed", and only the table can tell
    them apart: a live active row means duplicate (409, carrying its id so the
    page can jump to it), no active row means the write itself failed (503).
    """
    new_id = derivatives_db.insert_composing(new)
    if new_id:
        return str(new_id)
    existing = derivatives_db.find_active(new.idea_id, new.product_key, new.format)
    if existing is None:
        raise HTTPException(status_code=503, detail="could not create the spotlight")
    raise HTTPException(
        status_code=409,
        detail={
            "message": "this product already has an active spotlight for this post",
            "existing_id": str(existing.get("id") or ""),
        },
    )


def dispatch_compose(
    derivative_id: str,
    *,
    log: logging.Logger,
    label: str,
    script: str,
    brand_id: str,
    brand_dir: str,
    timeout_seconds: int,
) -> None:
    """Push the compose run, or fail the row it would have composed.

    The row is created BEFORE this push (the reverse of
    `social_posts_retry_api`) because the script's only argument is the row's
    id. That ordering is only safe because a failed push is turned into
    ``'failed'`` here and now: leaving the row ``'composing'`` would hold the
    partial unique index for the full 20-minute stale window and answer 409 to
    every retry of a button that never actually ran.
    """
    try:
        flow_queue.dispatch(
            schedule_task_id=label,
            script=script,
            args=["--derivative-id", derivative_id],
            brand=brand_id,
            brand_dir=brand_dir,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # a dead queue arrives in many shapes; all mean 503
        derivatives_db.mark_failed(derivative_id, error="dispatch_failed")
        log_event(
            log,
            "social_derivative_dispatch_failed",
            brand=brand_id,
            derivative_id=derivative_id,
            error=f"{type(exc).__name__}: {exc}"[:200],
        )
        raise HTTPException(status_code=503, detail="could not queue the compose run") from exc


def validated_category(brand_dir: str, requested: str) -> str:
    """The requested photo-collection slug, proven to hold at least one photo.

    Exactly `api.social_posts_retry_api._validated_category`: a
    declared-but-empty tag reads as a real choice in the picker and then
    resolves to no image at all, so it is a 422 rather than a silent ignore.
    ``""`` means "let the plan decide" and passes straight through.
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


def scan_post(
    idea: Mapping[str, Any], log: logging.Logger
) -> tuple[PostScan, dict[str, Any] | None]:
    """Fetch the live post, or report that WordPress could not be reached.

    Degraded success, never an error: `post_scan="unavailable"` still lists the
    whole catalog with every `in_post` false, which is a usable picker. Failing
    the route instead would make the feature unusable in exactly the container
    it is served from, whose environment carries no WP credentials at all.
    """
    try:
        post = wp_source.fetch_post(str(idea.get("wp_post_id") or ""))
    except WP_UNAVAILABLE as exc:
        log_event(
            log,
            "social_derivative_post_scan_unavailable",
            idea_id=str(idea.get("id") or ""),
            error=f"{type(exc).__name__}: {exc}"[:200],
        )
        return "unavailable", None
    if post is None:
        return "unavailable", None
    return "ok", post


def rendered_html(post: Mapping[str, Any] | None) -> str:
    """``post["content"]["rendered"]``, defensively -- WP omits `content` for a
    post the credentials may see the metadata of but not the body of."""
    if not post:
        return ""
    content = post.get("content")
    if not isinstance(content, Mapping):
        return ""
    return str(content.get("rendered") or "")


def ordered_products(
    pool: Mapping[str, ProductEntry],
    in_post: Sequence[ProductEntry],
    *,
    scope: ProductScope,
    category: str,
) -> list[SpotlightProduct]:
    """The picker's list: in-post products first, then the filtered catalog.

    In-post entries keep the post's own link order (that is the order the
    article argues them in, and the owner is reading the article) and are NEVER
    filtered out by `scope="category"` -- a product the post actually links is
    relevant to the post whatever the catalog files it under. Everything else
    sorts by display name, case-insensitively, because the dialog is a list a
    human scans.

    An empty `category` disables the filter rather than matching nothing: an
    uncategorised idea at a brand with no focus would otherwise show an empty
    catalog, which reads as "you have no products".
    """
    in_post_keys = {entry.key for entry in in_post}
    rest = sorted(
        (entry for entry in pool.values() if entry.key not in in_post_keys),
        key=lambda entry: entry.display.casefold(),
    )
    if scope == "category" and category:
        rest = [entry for entry in rest if slugify(entry.category or "") == category]
    return [SpotlightProduct.from_entry(entry, in_post=True) for entry in in_post] + [
        SpotlightProduct.from_entry(entry, in_post=False) for entry in rest
    ]


def resolve_under(root: Path, relative: str) -> Path | None:
    """``root/relative`` when it stays under ``root``, else ``None``.

    A derivative row is worker-written data and `image_path` is a string in it;
    a `../` that got there through any path at all must resolve to a 404, not
    to a file read. `is_relative_to` on the RESOLVED pair is the check --
    comparing the unresolved strings would be fooled by the first symlink.
    """
    if not relative:
        return None
    base = root.resolve()
    target = (base / relative).resolve()
    return target if target.is_relative_to(base) else None
