"""Tests for `lib.social_post_db` — the FB+IG post track on content_ideas.

Same mocking convention as `test_ideas_db_reels.py`: every `lib.db` call is
mocked, no real Postgres connection is ever opened.
"""
# ruff: noqa: S101

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from lib import social_post_db


@patch("lib.social_post_db.db.execute")
def test_claim_wins_only_from_null(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    assert social_post_db.claim("idea-1") is True
    query, params = mock_execute.call_args[0]
    assert "SET social_post_status = 'composing'" in query
    assert "WHERE id = %s AND social_post_status IS NULL" in query
    assert params == ("idea-1",)


@patch("lib.social_post_db.db.execute")
def test_claim_lost_returns_false(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 0
    assert social_post_db.claim("idea-1") is False


@patch("lib.social_post_db.db.execute")
def test_claim_catches_exception(mock_execute: MagicMock) -> None:
    mock_execute.side_effect = Exception("boom")
    assert social_post_db.claim("idea-1") is False


@patch("lib.social_post_db.db.execute")
def test_revert_claim_only_from_composing(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    assert social_post_db.revert_claim("idea-1") is True
    query, _ = mock_execute.call_args[0]
    assert "SET social_post_status = NULL" in query
    assert "AND social_post_status = 'composing'" in query


@patch("lib.social_post_db.db.execute")
def test_set_pending_review_guards_on_composing(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    assert (
        social_post_db.set_pending_review(
            "idea-1",
            fb_caption="fb",
            ig_caption="ig",
            image_path="state/social_posts_pending/idea-1.jpg",
            image_alt="alt",
            source="gemini",
            validation_flags=["flag"],
        )
        is True
    )
    query, params = mock_execute.call_args[0]
    assert "SET social_post_status = 'queued'" in query
    assert "AND social_post_status = 'composing'" in query
    assert params[0] == "fb"
    assert params[1] == "ig"
    assert params[-1] == "idea-1"


@patch("lib.social_post_db.db.execute")
def test_set_pending_review_empty_flags_stored_as_null(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    social_post_db.set_pending_review(
        "idea-1",
        fb_caption="fb",
        ig_caption="ig",
        image_path="p.jpg",
        image_alt="alt",
        source="gemini",
        validation_flags=[],
    )
    _, params = mock_execute.call_args[0]
    assert params[5] is None  # empty list -> NULL, matching set_reel_pending_review


@patch("lib.social_post_db.db.execute")
def test_reject_is_terminal_not_a_reset(mock_execute: MagicMock) -> None:
    """The one deliberate divergence from reject_reel: the row moves to
    'rejected', it is NOT reset to NULL — a reset would put it straight back
    into list_candidates and the pipeline would regenerate it forever."""
    mock_execute.return_value = 1
    assert social_post_db.reject("idea-1") is True
    query, _ = mock_execute.call_args[0]
    assert "SET social_post_status = 'rejected'" in query
    assert "AND social_post_status = 'queued'" in query
    assert "NULL" not in query.upper().replace("IS NULL", "")


@patch("lib.social_post_db.db.execute")
def test_schedule_fb_claims_slot_from_queued(mock_execute: MagicMock) -> None:
    due = datetime(2026, 8, 11, 13, tzinfo=UTC)
    mock_execute.return_value = 1
    assert social_post_db.schedule_fb("idea-1", due_at=due) is True
    query, params = mock_execute.call_args[0]
    assert "SET social_post_status = 'scheduled'" in query
    assert "social_post_fb_due_at = %s" in query
    assert "AND social_post_status = 'queued'" in query
    assert params == (due, "idea-1")


@patch("lib.social_post_db.db.execute")
def test_schedule_fb_double_click_is_noop(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 0  # already 'scheduled'
    assert social_post_db.schedule_fb("idea-1", due_at=datetime.now(UTC)) is False


@patch("lib.social_post_db.db.execute")
def test_unschedule_releases_slot_back_to_queued(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    assert social_post_db.unschedule_fb("idea-1") is True
    query, _ = mock_execute.call_args[0]
    assert "SET social_post_status = 'queued'" in query
    assert "social_post_fb_due_at = NULL" in query
    assert "AND social_post_status = 'scheduled'" in query


@patch("lib.social_post_db.db.fetch_one")
def test_last_scheduled_slot_spans_all_slot_holding_statuses(mock_fetch: MagicMock) -> None:
    """Published rows still hold their slot -- otherwise a new approval could
    land on top of a post that already went out."""
    mock_fetch.return_value = {"slot": datetime(2026, 8, 12, 13, tzinfo=UTC)}
    got = social_post_db.last_scheduled_fb_slot(brand_id="b")
    assert got == datetime(2026, 8, 12, 13, tzinfo=UTC)
    query, params = mock_fetch.call_args[0]
    assert "MAX(social_post_fb_due_at)" in query
    assert list(params[0]) == ["scheduled", "fb_published", "published"]


@patch("lib.social_post_db.db.fetch_one")
def test_last_scheduled_slot_none_when_empty(mock_fetch: MagicMock) -> None:
    mock_fetch.return_value = {"slot": None}
    assert social_post_db.last_scheduled_fb_slot() is None


@patch("lib.social_post_db.db.fetch_all")
def test_list_due_for_fb_uses_slot_time(mock_fetch: MagicMock) -> None:
    mock_fetch.return_value = []
    social_post_db.list_due_for_fb()
    query, _ = mock_fetch.call_args[0]
    assert "social_post_status = 'scheduled'" in query
    assert "social_post_fb_due_at <= NOW()" in query


@patch("lib.social_post_db.db.execute")
def test_set_fb_result_arms_ig_and_guards_on_scheduled(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    assert social_post_db.set_fb_result("idea-1", url="https://fb/p", ig_gap_hours=4.0) is True
    query, params = mock_execute.call_args[0]
    assert "SET social_post_status = 'fb_published'" in query
    assert "social_post_ig_due_at = NOW() + make_interval" in query
    assert "AND social_post_status = 'scheduled'" in query
    assert params == ("https://fb/p", 4.0 * 3600.0, "idea-1")


@patch("lib.social_post_db.db.execute")
def test_set_fb_result_repeat_sweep_is_noop(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 0  # row no longer 'scheduled'
    assert social_post_db.set_fb_result("idea-1", url="u", ig_gap_hours=4.0) is False


@patch("lib.social_post_db.db.execute")
def test_set_ig_result_terminal_publish(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    assert social_post_db.set_ig_result("idea-1", url="https://ig/p") is True
    query, params = mock_execute.call_args[0]
    assert "SET social_post_status = 'published'" in query
    assert "AND social_post_status = 'fb_published'" in query
    assert params == ("https://ig/p", "idea-1")


@patch("lib.social_post_db.db.fetch_all")
def test_list_candidates_filters_started_tracks(mock_fetch: MagicMock) -> None:
    mock_fetch.return_value = []
    social_post_db.list_candidates(brand_id="b", limit=5)
    query, params = mock_fetch.call_args[0]
    assert "social_post_status IS NULL" in query
    assert "wp_url IS NOT NULL" in query
    assert list(params[0]) == ["wp_published", "social_done"]


@patch("lib.social_post_db.db.fetch_all")
def test_list_due_for_ig_uses_due_time(mock_fetch: MagicMock) -> None:
    mock_fetch.return_value = []
    social_post_db.list_due_for_ig()
    query, _ = mock_fetch.call_args[0]
    assert "social_post_status = 'fb_published'" in query
    assert "social_post_ig_due_at <= NOW()" in query


@patch("lib.social_post_db.db.fetch_all")
def test_list_candidates_defensive_on_error(mock_fetch: MagicMock) -> None:
    mock_fetch.side_effect = Exception("boom")
    assert social_post_db.list_candidates() == []
