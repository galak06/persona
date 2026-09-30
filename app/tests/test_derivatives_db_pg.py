"""Integration tests for `lib.derivatives_db` against a real Postgres.

`test_derivatives_db.py` proves each statement says what we think it says.
This file proves the *database* agrees — and it is the only place that can,
because three of the guarantees live in the server, not in Python:

* the partial unique index `uq_content_derivatives_active`, which is what makes
  create idempotent. A predicate mismatch between the index and the
  `ON CONFLICT ... WHERE` in `writes.insert_composing` does not fail a mocked
  test: Postgres simply refuses to infer the arbiter and raises;
* the foreign key on `idea_id`, which is the reason an orphan derivative cannot
  exist;
* the database clock — `fb_due_at <= NOW()`, `NOW() + make_interval(secs => ...)`
  and the stale-compose sweep all compare server time to server time.

Rows are driven through the PUBLIC helpers wherever the state machine allows
it, so what is exercised is the real sequence a compose/approve/publish run
follows. Raw UPDATEs appear only to arrange a clock (backdating `updated_at`),
never to reach a status the helpers could have produced. Gated by `tests._pg`;
the suite only ever runs against the derived, suite-owned `*_test` database
that `conftest.py` creates.
"""
# ruff: noqa: S101

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from lib import db, derivatives_db
from lib.derivatives_db import NewDerivative
from tests._pg import requires_postgres

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"

_IDEA_ID = "idea-spotlight-1"
_BRAND = "brand-spotlight"

#: What a finished compose hands `set_pending_review`.
_REVIEW: dict[str, Any] = {
    "fb_caption": "fb caption",
    "ig_caption": "ig caption",
    "comment_keyword": "GREENIES",
    "image_path": "state/social_posts_pending/spotlight.jpg",
    "image_alt": "alt",
    "source": "gemini",
    "validation_flags": ["product_not_in_post"],
    "fb_affiliate_url": "https://amzn.to/fb",
    "ig_affiliate_url": "https://amzn.to/ig",
}


@pytest.fixture
def seeded_idea() -> Iterator[str]:
    """A clean pair of tables plus the one published post every row hangs off.

    `content_derivatives.idea_id` is a real FK, so a derivative cannot be
    inserted without this row — which is itself one of the things under test.
    """
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    db.execute("TRUNCATE TABLE content_derivatives, content_ideas CASCADE")
    db.execute(
        "INSERT INTO content_ideas (id, category, topic, status, brand_id, wp_url) "
        "VALUES (%s, 'dog-food', 'Best dental chews for big dogs', 'wp_published', %s, %s)",
        (_IDEA_ID, _BRAND, "https://dogfoodandfun.com/best-dental-chews/"),
    )
    try:
        yield _IDEA_ID
    finally:
        db.execute("TRUNCATE TABLE content_derivatives, content_ideas CASCADE")


def _new(*, product_key: str = "greenies-dental", idea_id: str = _IDEA_ID) -> NewDerivative:
    return NewDerivative(
        idea_id=idea_id,
        brand_id=_BRAND,
        product_key=product_key,
        product_asin="B000AAA111",
        product_display="Greenies Dental Treats (Regular)",
    )


def _row(derivative_id: str) -> dict[str, Any]:
    row = derivatives_db.get(derivative_id)
    assert row is not None
    return row


def _queued(product_key: str = "greenies-dental") -> str:
    """A composed row sitting in review — the state approval starts from."""
    new_id = derivatives_db.insert_composing(_new(product_key=product_key))
    assert new_id is not None
    assert derivatives_db.set_pending_review(new_id, **_REVIEW)
    return new_id


def _scheduled(*, due_at: datetime, product_key: str = "greenies-dental") -> str:
    new_id = _queued(product_key)
    assert derivatives_db.schedule_fb(new_id, due_at=due_at)
    return new_id


def _past() -> datetime:
    return datetime.now(UTC) - timedelta(minutes=5)


def _future() -> datetime:
    return datetime.now(UTC) + timedelta(hours=5)


# The partial unique index — what makes create idempotent
@requires_postgres
def test_second_active_insert_is_refused(seeded_idea: str) -> None:
    first = derivatives_db.insert_composing(_new())
    assert first is not None
    assert derivatives_db.insert_composing(_new()) is None
    found = derivatives_db.find_active(_IDEA_ID, "greenies-dental", "feed_post")
    assert found is not None
    assert found["id"] == first  # the id the API returns in its 409


@requires_postgres
def test_scheduled_still_blocks_a_duplicate(seeded_idea: str) -> None:
    """Approved-but-not-yet-out is still the same pending post."""
    _scheduled(due_at=_future())
    assert derivatives_db.insert_composing(_new()) is None


@requires_postgres
def test_a_different_product_is_never_a_duplicate(seeded_idea: str) -> None:
    assert derivatives_db.insert_composing(_new()) is not None
    assert derivatives_db.insert_composing(_new(product_key="kong-classic")) is not None


@requires_postgres
@pytest.mark.parametrize("terminal", ["failed", "rejected", "published"])
def test_terminal_rows_allow_the_spotlight_again(seeded_idea: str, terminal: str) -> None:
    """'failed' and 'rejected' must not block the retry they exist to allow,
    and spotlighting the same product again next month is a feature."""
    if terminal == "failed":
        first = derivatives_db.insert_composing(_new())
        assert first is not None
        assert derivatives_db.mark_failed(first, error="plan_failed")
    elif terminal == "rejected":
        first = _queued()
        assert derivatives_db.reject(first)
    else:
        first = _scheduled(due_at=_past())
        assert derivatives_db.claim_publish(first, "fb")
        assert derivatives_db.set_fb_result(first, url="https://fb/p", ig_gap_hours=0.0)
        assert derivatives_db.claim_publish(first, "ig")
        assert derivatives_db.set_ig_result(first, url="https://ig/p")

    assert _row(first)["status"] == terminal
    assert derivatives_db.insert_composing(_new()) is not None


@requires_postgres
def test_orphan_derivative_is_refused_by_the_foreign_key(seeded_idea: str) -> None:
    """No source post means no caption grounding and no url for the review
    card, so an orphan is always a bug — the database refuses it and the
    defensive wrapper turns that into None rather than an exception."""
    assert derivatives_db.insert_composing(_new(idea_id="no-such-idea")) is None


# Transition guards
@requires_postgres
def test_schedule_fb_only_moves_a_queued_row(seeded_idea: str) -> None:
    new_id = derivatives_db.insert_composing(_new())
    assert new_id is not None
    assert derivatives_db.schedule_fb(new_id, due_at=_future()) is False  # still composing
    assert _row(new_id)["fb_due_at"] is None

    assert derivatives_db.set_pending_review(new_id, **_REVIEW)
    slot = _future()
    assert derivatives_db.schedule_fb(new_id, due_at=slot) is True
    assert derivatives_db.schedule_fb(new_id, due_at=slot) is False  # double click
    assert _row(new_id)["status"] == "scheduled"

    assert derivatives_db.unschedule_fb(new_id) is True
    back = _row(new_id)
    assert back["status"] == "queued"
    assert back["fb_due_at"] is None


@requires_postgres
def test_claim_publish_refuses_a_row_that_is_not_due_yet(seeded_idea: str) -> None:
    """The due check lives inside the UPDATE, so a slot that has not arrived is
    refused even though the row is perfectly 'scheduled'."""
    new_id = _scheduled(due_at=_future())
    assert derivatives_db.claim_publish(new_id, "fb") is False
    assert _row(new_id)["status"] == "scheduled"
    assert derivatives_db.list_due_for_fb(brand_id=_BRAND) == []

    assert derivatives_db.unschedule_fb(new_id)
    assert derivatives_db.schedule_fb(new_id, due_at=_past())
    assert [r["id"] for r in derivatives_db.list_due_for_fb(brand_id=_BRAND)] == [new_id]
    assert derivatives_db.claim_publish(new_id, "fb") is True
    assert derivatives_db.claim_publish(new_id, "fb") is False  # a second sweep loses
    assert _row(new_id)["status"] == "fb_publishing"

    assert derivatives_db.release_publish_claim(new_id, "fb") is True
    released = _row(new_id)
    assert released["status"] == "scheduled"
    assert released["fb_due_at"] is not None  # slot kept: due again next sweep


@requires_postgres
def test_set_fb_result_needs_the_claim_and_stamps_the_ig_gap(seeded_idea: str) -> None:
    new_id = _scheduled(due_at=_past())
    assert derivatives_db.set_fb_result(new_id, url="https://fb/p", ig_gap_hours=4.0) is False

    assert derivatives_db.claim_publish(new_id, "fb")
    assert derivatives_db.set_fb_result(new_id, url="https://fb/p", ig_gap_hours=4.0) is True
    row = _row(new_id)
    assert row["status"] == "fb_published"
    assert row["fb_page_post_url"] == "https://fb/p"
    gap = row["ig_due_at"] - datetime.now(UTC)
    assert timedelta(hours=3, minutes=50) < gap < timedelta(hours=4, minutes=10)

    # The IG half is armed but not due, so nothing may claim it yet.
    assert derivatives_db.list_due_for_ig(brand_id=_BRAND) == []
    assert derivatives_db.claim_publish(new_id, "ig") is False


@requires_postgres
def test_fail_stale_composing_only_touches_rows_that_stopped_moving(seeded_idea: str) -> None:
    fresh = derivatives_db.insert_composing(_new())
    stale = derivatives_db.insert_composing(_new(product_key="kong-classic"))
    assert fresh is not None
    assert stale is not None
    db.execute(
        "UPDATE content_derivatives SET updated_at = NOW() - make_interval(secs => 2400) "
        "WHERE id = %s",
        (stale,),
    )

    assert derivatives_db.fail_stale_composing(brand_id=_BRAND) == 1
    assert _row(fresh)["status"] == "composing"
    swept = _row(stale)
    assert swept["status"] == "failed"
    assert swept["error"] == derivatives_db.STALE_COMPOSING_ERROR
    assert derivatives_db.fail_stale_composing(brand_id="another-brand") == 0


# Reads that only a real server can prove
@requires_postgres
def test_last_scheduled_fb_slot_is_the_max_across_slot_holding_rows(seeded_idea: str) -> None:
    assert derivatives_db.last_scheduled_fb_slot(brand_id=_BRAND) is None

    early = datetime.now(UTC) + timedelta(hours=6)
    late = datetime.now(UTC) + timedelta(hours=30)
    _scheduled(due_at=late, product_key="kong-classic")
    _scheduled(due_at=early)

    got = derivatives_db.last_scheduled_fb_slot(brand_id=_BRAND)
    assert got is not None
    assert abs(got - late) < timedelta(seconds=1)
    assert derivatives_db.last_scheduled_fb_slot(brand_id="another-brand") is None


@requires_postgres
def test_review_listing_joins_the_post_and_widens_only_for_queued(seeded_idea: str) -> None:
    queued = _queued()
    composing = derivatives_db.insert_composing(_new(product_key="kong-classic"))
    failed = derivatives_db.insert_composing(_new(product_key="nylabone"))
    assert composing is not None
    assert failed is not None
    assert derivatives_db.mark_failed(failed, error="plan_failed")

    rows = derivatives_db.list_for_review(brand_id=_BRAND)
    assert {r["id"] for r in rows} == {queued, composing, failed}
    assert rows[0]["topic"] == "Best dental chews for big dogs"
    assert rows[0]["wp_url"] == "https://dogfoodandfun.com/best-dental-chews/"

    # A failed row older than FAILED_VISIBLE_HOURS drops out of the listing.
    db.execute(
        "UPDATE content_derivatives SET updated_at = NOW() - make_interval(hours => 48) "
        "WHERE id = %s",
        (failed,),
    )
    assert {r["id"] for r in derivatives_db.list_for_review(brand_id=_BRAND)} == {
        queued,
        composing,
    }

    # Every other status is exact: no widening, no composing rows.
    assert [r["id"] for r in derivatives_db.list_for_review(brand_id=_BRAND, status="failed")] == [
        failed
    ]


@requires_postgres
def test_an_unreleased_publish_claim_stays_visible_on_the_review_list(seeded_idea: str) -> None:
    """A worker SIGKILLed between `claim_publish` and its result write leaves
    the row in `fb_publishing`/`ig_publishing` for good -- nothing reaps either.
    Hiding them would make the spotlight vanish with NOTHING published, which is
    a silent loss, not the deliberate `wedged` stop. Listing is all that happens
    here: no auto-release, because the claim may belong to a post that did go
    live and republishing it is worse than showing a stuck card.
    """
    fb_stuck = _scheduled(due_at=_past())
    assert derivatives_db.claim_publish(fb_stuck, "fb")

    ig_stuck = _scheduled(due_at=_past(), product_key="kong-classic")
    assert derivatives_db.claim_publish(ig_stuck, "fb")
    assert derivatives_db.set_fb_result(ig_stuck, url="https://fb/p", ig_gap_hours=0.0)
    assert derivatives_db.claim_publish(ig_stuck, "ig")

    queued = _queued(product_key="nylabone")

    rows = derivatives_db.list_for_review(brand_id=_BRAND)
    assert {r["id"] for r in rows} == {fb_stuck, ig_stuck, queued}
    assert {r["status"] for r in rows} == {"fb_publishing", "ig_publishing", "queued"}
    # The join still holds for them -- the card needs the post it came from.
    assert all(r["topic"] == "Best dental chews for big dogs" for r in rows)

    # Still exact for the statuses themselves, and still no widening elsewhere.
    assert [
        r["id"] for r in derivatives_db.list_for_review(brand_id=_BRAND, status="fb_publishing")
    ] == [fb_stuck]
    assert derivatives_db.list_for_review(brand_id=_BRAND, status="scheduled") == []
