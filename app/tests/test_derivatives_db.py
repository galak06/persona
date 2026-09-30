"""Tests for `lib.derivatives_db` — the content_derivatives state machine.

Same mocking convention as `test_social_post_db.py`: every `lib.db` call is
mocked, no real Postgres connection is ever opened. What is protected here is
the GUARD, not the round trip — each transition is one conditional
`UPDATE ... WHERE id = %s AND status = <prior>`, and a guard silently widened
(or a WHERE clause lost in a refactor) is exactly the double-post race this
table exists to close. `test_derivatives_db_pg.py` proves the same SQL against
a real server. The closing table proves no helper raises, so a bookkeeping
failure cannot take a compose, a page load or a release sweep down with it.
"""
# ruff: noqa: S101

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lib import derivatives_db
from lib.derivatives_db import NewDerivative

_NEW = NewDerivative(
    idea_id="idea-1",
    brand_id="brand-1",
    product_key="greenies-dental",
    product_asin="B000AAA111",
    product_display="Greenies Dental Treats (Regular)",
    reference_category="kitchen",
)

_DUE = datetime(2026, 9, 18, 13, tzinfo=UTC)

_REVIEW: dict[str, Any] = {
    "fb_caption": "fb",
    "ig_caption": "ig",
    "comment_keyword": "GREENIES",
    "image_path": "state/social_posts_pending/spotlight-d1.jpg",
    "image_alt": "alt",
    "source": "gemini",
    "validation_flags": ["product_not_in_post"],
    "fb_affiliate_url": "https://amzn.to/fb",
    "ig_affiliate_url": "https://amzn.to/ig",
}


@patch("lib.derivatives_db.reads.db.fetch_one")
def test_get_by_id_and_find_active_by_the_index_key(mock_fetch: MagicMock) -> None:
    mock_fetch.return_value = {"id": "d1"}
    assert derivatives_db.get("d1") == {"id": "d1"}
    query, params = mock_fetch.call_args[0]
    assert "SELECT * FROM content_derivatives WHERE id = %s" in query
    assert params == ("d1",)

    assert derivatives_db.find_active("idea-1", "greenies-dental", "feed_post") == {"id": "d1"}
    query, params = mock_fetch.call_args[0]
    assert "WHERE idea_id = %s AND product_key = %s AND format = %s AND status = ANY(%s)" in query
    assert "ORDER BY created_at DESC LIMIT 1" in query
    assert params[:3] == ("idea-1", "greenies-dental", "feed_post")
    assert list(params[3]) == ["composing", "queued", "scheduled"]


@patch("lib.derivatives_db.reads.db.fetch_all")
def test_list_for_review_widens_queued_with_composing_and_fresh_failed(
    mock_fetch: MagicMock,
) -> None:
    """The review listing is the ONLY status that returns more than it asks
    for: a composing row is the in-flight signal the 5 s poll watches, an
    unreleased publish claim would otherwise vanish with nothing published, and
    a recently failed row is how the owner learns WHY a spotlight never came."""
    mock_fetch.return_value = []
    derivatives_db.list_for_review(brand_id="brand-1", limit=25)
    query, params = mock_fetch.call_args[0]
    assert "JOIN content_ideas i ON i.id = d.idea_id" in query
    assert "i.topic AS topic" in query
    assert "i.wp_url AS wp_url" in query
    assert "%s = 'queued'" in query
    assert "d.status IN ('composing', 'fb_publishing', 'ig_publishing')" in query
    assert "d.updated_at > NOW() - make_interval(hours => %s)" in query
    assert "AND d.brand_id = %s" in query
    assert "ORDER BY d.created_at DESC LIMIT %s" in query
    assert params == ("queued", "queued", derivatives_db.FAILED_VISIBLE_HOURS, "brand-1", 25)

    derivatives_db.list_for_review(status="published", limit=10_000)
    query, params = mock_fetch.call_args[0]
    assert "AND d.brand_id = %s" not in query  # no brand -> no clause
    assert params[:2] == ("published", "published")  # the switch never fires
    assert params[-1] == 500  # limit clamped


@patch("lib.derivatives_db.reads.db.fetch_one")
@patch("lib.derivatives_db.reads.db.fetch_all")
def test_due_listings_and_the_shared_slot_read(mock_fetch: MagicMock, mock_one: MagicMock) -> None:
    """A row mid-publish still owns its slot — dropping it for those seconds
    would let a concurrent approval take the same hour."""
    mock_fetch.return_value = []
    derivatives_db.list_due_for_fb(brand_id="brand-1")
    query, params = mock_fetch.call_args[0]
    assert "status = 'scheduled'" in query
    assert "fb_due_at IS NOT NULL AND fb_due_at <= NOW()" in query
    assert "ORDER BY fb_due_at ASC" in query
    assert params == ("brand-1", 50)

    derivatives_db.list_due_for_ig()
    query, _ = mock_fetch.call_args[0]
    assert "status = 'fb_published'" in query
    assert "ig_due_at IS NOT NULL AND ig_due_at <= NOW()" in query
    assert "ORDER BY ig_due_at ASC" in query

    mock_one.return_value = {"slot": _DUE}
    assert derivatives_db.last_scheduled_fb_slot(brand_id="brand-1") == _DUE
    query, params = mock_one.call_args[0]
    assert "SELECT MAX(fb_due_at) AS slot FROM content_derivatives WHERE status = ANY(%s)" in query
    assert list(params[0]) == [
        "scheduled",
        "fb_publishing",
        "fb_published",
        "ig_publishing",
        "published",
    ]
    assert params[1] == "brand-1"
    mock_one.return_value = {"slot": None}
    assert derivatives_db.last_scheduled_fb_slot() is None


@patch("lib.derivatives_db.writes.db.execute")
def test_insert_composing_mints_id_and_infers_the_partial_index(mock_execute: MagicMock) -> None:
    """The ON CONFLICT predicate must match uq_content_derivatives_active byte
    for byte or Postgres refuses to infer it as the arbiter. Zero rows is the
    duplicate signal — the caller disambiguates with find_active, which is why
    it is None and not an exception."""
    mock_execute.return_value = 1
    new_id = derivatives_db.insert_composing(_NEW)
    query, params = mock_execute.call_args[0]
    assert "ON CONFLICT (idea_id, product_key, format) " in query
    assert "WHERE status IN ('composing', 'queued', 'scheduled') " in query
    assert "DO NOTHING" in query
    assert params[0] == new_id
    assert params[1:] == (
        "idea-1",
        "brand-1",
        "product_spotlight",
        "feed_post",
        "greenies-dental",
        "B000AAA111",
        "Greenies Dental Treats (Regular)",
        "kitchen",
    )
    mock_execute.return_value = 0
    assert derivatives_db.insert_composing(_NEW) is None

    mock_execute.return_value = 1
    assert derivatives_db.mark_failed("d1", error="x" * 900) is True
    query, params = mock_execute.call_args[0]
    assert "SET status = 'failed', error = %s" in query
    assert "WHERE id = %s AND status = 'composing'" in query
    assert params == ("x" * 500, "d1")

    # The sweep compares NOW() to NOW(): container, worker and DB clocks differ.
    mock_execute.return_value = 3
    assert derivatives_db.fail_stale_composing(brand_id="brand-1") == 3
    query, params = mock_execute.call_args[0]
    assert "WHERE status = 'composing' AND brand_id = %s" in query
    assert "AND updated_at < NOW() - make_interval(secs => %s)" in query
    assert params == ("compose_timeout", "brand-1", 1200.0)


@patch("lib.derivatives_db.writes.db.execute")
def test_set_pending_review_guards_on_composing(mock_execute: MagicMock) -> None:
    """A refused write (False) is what tells the composer to unlink the image
    it just wrote, so a swept row leaves no orphan file."""
    mock_execute.return_value = 1
    assert derivatives_db.set_pending_review("d1", **_REVIEW) is True
    query, params = mock_execute.call_args[0]
    assert "SET status = 'queued'" in query
    assert "error = NULL" in query
    assert "updated_at = NOW()" in query
    assert "WHERE id = %s AND status = 'composing'" in query
    assert params[0] == "fb"
    assert params[2] == "GREENIES"
    assert params[6] == ["product_not_in_post"]
    assert params[7:] == ("https://amzn.to/fb", "https://amzn.to/ig", "d1")

    derivatives_db.set_pending_review("d1", **{**_REVIEW, "validation_flags": []})
    _, params = mock_execute.call_args[0]
    assert params[6] is None  # empty list -> NULL, matching social_post_db

    mock_execute.return_value = 0
    assert derivatives_db.set_pending_review("d1", **_REVIEW) is False


@patch("lib.derivatives_db.writes.db.execute")
def test_queued_exits_are_schedule_unschedule_and_reject(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    assert derivatives_db.schedule_fb("d1", due_at=_DUE) is True
    query, params = mock_execute.call_args[0]
    assert "SET status = 'scheduled', fb_due_at = %s" in query
    assert "WHERE id = %s AND status = 'queued'" in query
    assert params == (_DUE, "d1")

    assert derivatives_db.unschedule_fb("d1") is True
    query, _ = mock_execute.call_args[0]
    assert "SET status = 'queued', fb_due_at = NULL" in query
    assert "WHERE id = %s AND status = 'scheduled'" in query

    assert derivatives_db.reject("d1") is True  # the other exit from 'queued'
    query, params = mock_execute.call_args[0]
    assert "SET status = 'rejected'" in query
    assert "WHERE id = %s AND status = 'queued'" in query
    assert params == ("d1",)

    mock_execute.return_value = 0  # already scheduled: a double click is a no-op
    assert derivatives_db.schedule_fb("d1", due_at=_DUE) is False


@patch("lib.derivatives_db.publish.db.execute")
def test_claim_publish_checks_due_inside_the_update(mock_execute: MagicMock) -> None:
    """The due check is INSIDE the claim so a slot moved between the sweep's
    SELECT and this call is refused rather than published early."""
    mock_execute.return_value = 1
    assert derivatives_db.claim_publish("d1", "fb") is True
    query, params = mock_execute.call_args[0]
    assert "SET status = 'fb_publishing'" in query
    assert "WHERE id = %s AND status = 'scheduled'" in query
    assert "AND fb_due_at IS NOT NULL AND fb_due_at <= NOW()" in query
    assert params == ("d1",)

    mock_execute.return_value = 0  # another sweep already holds it
    assert derivatives_db.claim_publish("d1", "ig") is False
    query, _ = mock_execute.call_args[0]
    assert "SET status = 'ig_publishing'" in query
    assert "WHERE id = %s AND status = 'fb_published'" in query
    assert "AND ig_due_at IS NOT NULL AND ig_due_at <= NOW()" in query


@patch("lib.derivatives_db.publish.db.execute")
def test_release_claim_puts_each_half_back(mock_execute: MagicMock) -> None:
    """Released ONLY when the publisher raised — an unknown platform is a
    warning and a False, never an exception and never a stray UPDATE."""
    assert derivatives_db.claim_publish("d1", "tiktok") is False  # type: ignore[arg-type]
    assert derivatives_db.release_publish_claim("d1", "x") is False  # type: ignore[arg-type]
    mock_execute.assert_not_called()

    mock_execute.return_value = 1
    assert derivatives_db.release_publish_claim("d1", "fb") is True
    query, _ = mock_execute.call_args[0]
    assert "SET status = 'scheduled'" in query
    assert "WHERE id = %s AND status = 'fb_publishing'" in query
    assert "fb_due_at" not in query  # the slot is left alone: due again next sweep

    assert derivatives_db.release_publish_claim("d1", "ig") is True
    query, _ = mock_execute.call_args[0]
    assert "SET status = 'fb_published'" in query
    assert "WHERE id = %s AND status = 'ig_publishing'" in query

    # make_interval(hours => ...) is integer-only and the gap is a float, so
    # the IG arming interval is built in seconds — same as lib.social_post_db.
    assert derivatives_db.set_fb_result("d1", url="https://fb/p", ig_gap_hours=4.0) is True
    query, params = mock_execute.call_args[0]
    assert "SET status = 'fb_published', fb_page_post_url = %s" in query
    assert "ig_due_at = NOW() + make_interval(secs => %s)" in query
    assert "WHERE id = %s AND status = 'fb_publishing'" in query
    assert params == ("https://fb/p", 14400.0, "d1")

    assert derivatives_db.set_ig_result("d1", url="https://ig/p") is True
    query, params = mock_execute.call_args[0]
    assert "SET status = 'published', ig_post_url = %s" in query
    assert "WHERE id = %s AND status = 'ig_publishing'" in query
    assert params == ("https://ig/p", "d1")


#: (patched lib.db call, helper, args, kwargs, the value a failure must return).
_SWALLOW: list[tuple[str, str, tuple[Any, ...], dict[str, Any], Any]] = [
    ("reads.db.fetch_one", "get", ("d1",), {}, None),
    ("reads.db.fetch_all", "list_for_review", (), {}, []),
    ("reads.db.fetch_one", "find_active", ("i", "p", "feed_post"), {}, None),
    ("reads.db.fetch_all", "list_due_for_fb", (), {}, []),
    ("reads.db.fetch_all", "list_due_for_ig", (), {}, []),
    ("reads.db.fetch_one", "last_scheduled_fb_slot", (), {}, None),
    ("writes.db.execute", "insert_composing", (_NEW,), {}, None),
    ("writes.db.execute", "set_pending_review", ("d1",), _REVIEW, False),
    ("writes.db.execute", "mark_failed", ("d1",), {"error": "e"}, False),
    ("writes.db.execute", "fail_stale_composing", (), {"brand_id": "b"}, 0),
    ("writes.db.execute", "reject", ("d1",), {}, False),
    ("writes.db.execute", "schedule_fb", ("d1",), {"due_at": _DUE}, False),
    ("writes.db.execute", "unschedule_fb", ("d1",), {}, False),
    ("publish.db.execute", "claim_publish", ("d1", "fb"), {}, False),
    ("publish.db.execute", "release_publish_claim", ("d1", "ig"), {}, False),
    ("publish.db.execute", "set_fb_result", ("d1",), {"url": "u", "ig_gap_hours": 4.0}, False),
    ("publish.db.execute", "set_ig_result", ("d1",), {"url": "u"}, False),
]


@pytest.mark.parametrize(("target", "name", "args", "kwargs", "expected"), _SWALLOW)
def test_every_helper_swallows_database_errors(
    target: str, name: str, args: tuple[Any, ...], kwargs: dict[str, Any], expected: Any
) -> None:
    with patch(f"lib.derivatives_db.{target}", side_effect=Exception("boom")):
        assert getattr(derivatives_db, name)(*args, **kwargs) == expected
