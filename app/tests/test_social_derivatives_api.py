"""Tests for the product-spotlight REVIEW routes: list, image, approve,
unschedule, reject -- plus the one end-to-end walk through the whole feature.

The world (fake `content_derivatives` table, fake queue, fake ideas, fake
catalog, real reference library on disk, REAL shared-slot allocator over faked
readers) is built by `tests/test_social_derivatives_create_api.py`, because the
end-to-end test has to start where a real spotlight starts -- at the source
picker -- and a second copy of that world would let the two halves of one
resource drift apart silently.

Two properties carry the most weight here. Approve SCHEDULES into the SHARED
Facebook calendar, so a spotlight can never be handed an hour a regular social
post already claimed; and the image route resolves a worker-written path, so a
`../` in the row must be a 404 rather than a file read.
"""
# ruff: noqa: S101, SLF001

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from api import social_derivatives_api as review_api
from api import social_derivatives_create_api as create_api
from fastapi import HTTPException

from tests.test_social_derivatives_create_api import BRAND, CHEW_ASIN, IDEA_ID, build_world

IMAGE_REL = "state/social_posts_pending/spotlight-deriv-1.jpg"


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    state = build_world(monkeypatch, tmp_path)
    # The review routes read BRAND_DIR directly (`_brand_root`), the way the
    # composer writes it: a relative path both containers agree on.
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    return state


def compose_finished(world: dict[str, Any], derivative_id: str, *, with_image: bool = True) -> Path:
    """Stand in for the compose run: captions written, row back in review."""
    row = world["db"].rows[derivative_id]
    row.update(
        status="queued",
        fb_caption="a caption the operator reads",
        ig_caption="an ig caption",
        comment_keyword="CHEW",
        source="gemini",
        validation_flags=["product_not_in_post"],
        topic="Best dental chews",
        wp_url="https://dogfoodandfun.com/best-dental-chews/",
    )
    target = world["brand_dir"] / IMAGE_REL
    if with_image:
        row["image_path"] = IMAGE_REL
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"a-composed-hook-image")
    return target


def _create(**overrides: Any) -> Any:
    body = {"idea_id": IDEA_ID, "product_key": "dental-chew"}
    body.update(overrides)
    return create_api.create_spotlight(create_api.CreateSpotlightRequest(**body))


def _list(status: str = "queued", limit: int = 100) -> Any:
    """Called as a plain function, so FastAPI's `Query` defaults are passed
    explicitly -- the `test_social_posts_api.py` convention."""
    return review_api.list_social_derivatives(status=status, limit=limit)


# ── the whole path, once ────────────────────────────────────────────────────


def test_a_spotlight_walks_from_the_picker_to_a_scheduled_post_and_back_out(
    world: dict[str, Any],
) -> None:
    """One test for the seam every other test only touches a piece of: pick a
    post, pick a product, compose, review, schedule into the shared calendar,
    change your mind, reject. Each step's answer is the next step's input."""
    # 1. the post picker
    source = create_api.list_spotlight_sources().posts[0]
    assert source.idea_id == IDEA_ID

    # 2. the product picker -- the article's own products first, then the
    #    post's category; "show all categories" widens it to the whole catalog.
    narrow = create_api.list_spotlight_products(source.idea_id, scope="category")
    assert [p.key for p in narrow.products] == ["dental-chew", "toy-rope", "salmon-oil"]
    assert narrow.products[0].in_post is True
    assert len(create_api.list_spotlight_products(source.idea_id, scope="all").products) == 4

    # 3. create -> the row exists before the compose run is queued, because the
    #    row's id IS that run's only argument.
    created = _create(product_key=narrow.products[0].key)
    assert (created.status, world["events"]) == ("composing", ["insert", "push"])
    assert world["pushed"][0]["args"] == ["--derivative-id", created.id]
    assert world["pushed"][0]["timeout_seconds"] == create_api._COMPOSE_TIMEOUT_SECONDS
    assert world["queued"] == [(f"{BRAND}-social-derivative-compose", BRAND)]

    # 4. the review list carries the in-flight row: `composing` is what tells
    #    the page to keep polling.
    listing = _list()
    assert [(d.id, d.composing, d.has_image) for d in listing.derivatives] == [
        (created.id, True, False)
    ]

    # 5. compose lands.
    image = compose_finished(world, created.id)
    reviewed = _list().derivatives[0]
    assert (reviewed.composing, reviewed.has_image, reviewed.status) == (False, True, "queued")
    assert reviewed.product_asin == CHEW_ASIN
    assert reviewed.dm_disclosure  # the sentence the manual DM must carry

    # 6. approve -- into the calendar the REGULAR posts already occupy. The
    #    brand has a social post at 13:00 tomorrow, so this lands after it.
    world["regular_slot"] = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    decision = review_api.approve_social_derivative(created.id)
    assert decision.status == "scheduled"
    assert decision.fb_due_at is not None
    assert datetime.fromisoformat(decision.fb_due_at) > world["regular_slot"]

    # 7. changed your mind: the slot goes back.
    assert review_api.unschedule_social_derivative(created.id).status == "queued"
    assert world["db"].rows[created.id]["fb_due_at"] is None

    # 8. reject is terminal and takes the generated image with it.
    assert review_api.reject_social_derivative(created.id).status == "rejected"
    assert not image.exists()
    assert _list().derivatives == []


# ── list ────────────────────────────────────────────────────────────────────


def test_a_stale_compose_is_swept_before_the_list_is_read(world: dict[str, Any]) -> None:
    """A run the worker was killed under must surface as 'failed' on the next
    poll rather than spinning a 'composing' card forever."""
    _list()

    assert world["db"].swept == [BRAND]


def test_an_unknown_status_is_refused_with_the_valid_ones(world: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as exc:
        _list(status="approved")

    assert exc.value.status_code == 422
    assert "composing" in str(exc.value.detail)


def test_the_listing_is_scoped_to_this_brand(world: dict[str, Any]) -> None:
    created = _create()
    world["db"].rows[created.id]["brand_id"] = "another-brand"

    assert _list().derivatives == []


@pytest.mark.parametrize("stuck", ["fb_publishing", "ig_publishing"])
def test_a_claim_nothing_released_stays_on_the_review_list(
    world: dict[str, Any], stuck: str
) -> None:
    """The one failure where NOTHING is live: a worker killed between
    `claim_publish` and its result write leaves the row mid-publish forever and
    nothing reaps it. Dropped from the listing the spotlight would simply
    vanish, so it is SHOWN (the page renders it read-only) and never
    auto-released -- the claim may belong to a post that did go out."""
    created = _create()
    compose_finished(world, created.id)
    world["db"].rows[created.id]["status"] = stuck

    listed = _list().derivatives

    assert [(d.id, d.status) for d in listed] == [(created.id, stuck)]
    assert listed[0].composing is False  # not an in-flight compose: no polling


# ── image ───────────────────────────────────────────────────────────────────


def test_the_composed_image_is_served_uncached(world: dict[str, Any]) -> None:
    created = _create()
    compose_finished(world, created.id)

    response = review_api.get_social_derivative_image(created.id)

    assert response.media_type == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"


def test_a_row_with_no_image_is_a_404(world: dict[str, Any]) -> None:
    created = _create()
    compose_finished(world, created.id, with_image=False)

    with pytest.raises(HTTPException) as exc:
        review_api.get_social_derivative_image(created.id)

    assert exc.value.status_code == 404


def test_a_path_that_escapes_the_brand_root_is_a_404_not_a_file_read(
    world: dict[str, Any],
) -> None:
    """THE PATH PROPERTY. `image_path` is worker-written data; a `../` in it
    must never become a read of something outside the brand directory."""
    secret = world["brand_dir"].parent / "id_rsa"
    secret.write_bytes(b"not yours")
    created = _create()
    world["db"].rows[created.id]["image_path"] = "../id_rsa"

    with pytest.raises(HTTPException) as exc:
        review_api.get_social_derivative_image(created.id)

    assert exc.value.status_code == 404
    assert secret.exists()  # and nothing deleted it either


def test_an_unknown_spotlight_is_a_404(world: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as exc:
        review_api.get_social_derivative_image("nope")

    assert exc.value.status_code == 404


# ── approve / unschedule / reject ───────────────────────────────────────────


def test_approve_spaces_off_the_later_of_the_two_calendars(world: dict[str, Any]) -> None:
    """A spotlight and a regular post share the FB page's rate limit, so the
    allocator has to see BOTH tables or it hands out the same hour twice."""
    created = _create()
    compose_finished(world, created.id)
    world["regular_slot"] = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)

    due_at = datetime.fromisoformat(
        review_api.approve_social_derivative(created.id).fb_due_at or ""
    )

    assert due_at >= world["regular_slot"] + timedelta(hours=24)
    assert world["db"].rows[created.id]["status"] == "scheduled"


def test_approve_on_a_row_still_composing_is_refused(world: dict[str, Any]) -> None:
    """Nothing to approve yet -- there are no captions and no image on the row
    until the compose run writes them."""
    created = _create()

    with pytest.raises(HTTPException) as exc:
        review_api.approve_social_derivative(created.id)

    assert exc.value.status_code == 409
    assert "composing" in str(exc.value.detail)


def test_approve_loses_a_race_without_double_booking(world: dict[str, Any]) -> None:
    """`schedule_fb` is the real guard; the status read above it is only a
    friendlier message."""
    created = _create()
    compose_finished(world, created.id)
    db = world["db"]
    real_schedule = db.schedule_fb

    def _raced(derivative_id: str, *, due_at: Any) -> bool:
        db.rows[derivative_id]["status"] = "scheduled"  # another tab got there
        return real_schedule(derivative_id, due_at=due_at)

    db.schedule_fb = _raced  # type: ignore[method-assign]

    with pytest.raises(HTTPException) as exc:
        review_api.approve_social_derivative(created.id)

    assert exc.value.status_code == 409


def test_unschedule_on_a_row_that_never_held_a_slot_is_refused(world: dict[str, Any]) -> None:
    created = _create()
    compose_finished(world, created.id)

    with pytest.raises(HTTPException) as exc:
        review_api.unschedule_social_derivative(created.id)

    assert exc.value.status_code == 409


def test_reject_is_refused_once_a_slot_is_claimed(world: dict[str, Any]) -> None:
    created = _create()
    image = compose_finished(world, created.id)
    world["db"].rows[created.id]["status"] = "scheduled"

    with pytest.raises(HTTPException) as exc:
        review_api.reject_social_derivative(created.id)

    assert exc.value.status_code == 409
    assert image.exists()  # a scheduled post keeps its image


def test_reject_survives_an_image_that_is_already_gone(world: dict[str, Any]) -> None:
    """The row is terminal by then; a missing file must not turn a successful
    reject into a 500 the operator would retry."""
    created = _create()
    image = compose_finished(world, created.id)
    image.unlink()

    assert review_api.reject_social_derivative(created.id).status == "rejected"


def test_rejecting_frees_the_product_to_be_spotlighted_again(world: dict[str, Any]) -> None:
    """'rejected' sits outside the partial unique index on purpose."""
    first = _create()
    compose_finished(world, first.id)
    review_api.reject_social_derivative(first.id)

    second = _create()

    assert second.id != first.id and second.status == "composing"
