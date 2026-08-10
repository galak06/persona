"""Tests for the reels-crew additions to `lib.ideas_db` (§2 of the
WP-post -> IG/FB Reels plan) — `mark_wp_published`, `claim_idea_for_reel_composition`,
`set_reel_pending_review`, `reject_reel`, `set_reel_result`.

Same mocking convention as `test_ideas_db.py`: every `lib.db` call is
mocked, no real Postgres connection is ever opened.
"""
# ruff: noqa: S101

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lib import ideas_db


@patch("lib.ideas_db.db.execute")
def test_mark_wp_published_wins_claim(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    assert ideas_db.mark_wp_published("idea-1") is True
    query, params = mock_execute.call_args[0]
    assert "SET status = 'wp_published'" in query
    assert "WHERE id = %s AND status = 'wp_draft'" in query
    assert params == ("idea-1",)


@patch("lib.ideas_db.db.execute")
def test_mark_wp_published_false_when_not_wp_draft(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 0
    assert ideas_db.mark_wp_published("idea-1") is False


@patch("lib.ideas_db.db.execute")
def test_mark_wp_published_catches_exception_and_returns_false(mock_execute: MagicMock) -> None:
    mock_execute.side_effect = Exception("boom")
    assert ideas_db.mark_wp_published("idea-1") is False


@patch("lib.ideas_db.db.execute")
def test_claim_idea_for_reel_composition_wins_claim(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    assert ideas_db.claim_idea_for_reel_composition("idea-1") is True
    query, params = mock_execute.call_args[0]
    assert "SET status = 'composing_reel'" in query
    assert "WHERE id = %s AND status = 'wp_published'" in query
    assert params == ("idea-1",)


@patch("lib.ideas_db.db.execute")
def test_claim_idea_for_reel_composition_false_when_not_wp_published(
    mock_execute: MagicMock,
) -> None:
    mock_execute.return_value = 0
    assert ideas_db.claim_idea_for_reel_composition("idea-1") is False


@patch("lib.ideas_db.db.execute")
def test_claim_idea_for_reel_composition_catches_exception(mock_execute: MagicMock) -> None:
    mock_execute.side_effect = Exception("boom")
    assert ideas_db.claim_idea_for_reel_composition("idea-1") is False


@patch("lib.ideas_db.db.execute")
def test_set_reel_pending_review_builds_correct_update(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    ok = ideas_db.set_reel_pending_review(
        "idea-1",
        ig_video_path="/state/reels_pending/idea-1_ig.mp4",
        fb_video_path="/state/reels_pending/idea-1_fb.mp4",
        ig_caption="Check out this reel!",
        fb_caption="Check out this reel on FB!",
        source="fallback",
        validation_flags=["cure_claim"],
    )
    assert ok is True
    query, params = mock_execute.call_args[0]
    assert "SET status = 'social_queued'" in query
    assert "WHERE id = %s AND status = 'composing_reel'" in query
    assert params == (
        "/state/reels_pending/idea-1_ig.mp4",
        "/state/reels_pending/idea-1_fb.mp4",
        "Check out this reel!",
        "Check out this reel on FB!",
        "fallback",
        ["cure_claim"],
        "idea-1",
    )


@patch("lib.ideas_db.db.execute")
def test_set_reel_pending_review_defaults_empty_flags_to_none(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    ideas_db.set_reel_pending_review(
        "idea-1",
        ig_video_path="a.mp4",
        fb_video_path="b.mp4",
        ig_caption="ig",
        fb_caption="fb",
        source="openart",
    )
    _, params = mock_execute.call_args[0]
    assert params[-2] is None  # validation_flags column, not passed -> None


@patch("lib.ideas_db.db.execute")
def test_set_reel_pending_review_false_when_not_composing(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 0
    ok = ideas_db.set_reel_pending_review(
        "idea-1", ig_video_path="a.mp4", fb_video_path="b.mp4",
        ig_caption="ig", fb_caption="fb", source="openart",
    )
    assert ok is False


@patch("lib.ideas_db.db.execute")
def test_reject_reel_wins_claim_and_clears_columns(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    assert ideas_db.reject_reel("idea-1") is True
    query, params = mock_execute.call_args[0]
    assert "SET status = 'wp_published'" in query
    assert "reel_ig_video_path = NULL" in query
    assert "WHERE id = %s AND status = 'social_queued'" in query
    assert params == ("idea-1",)


@patch("lib.ideas_db.db.execute")
def test_reject_reel_false_when_not_social_queued(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 0
    assert ideas_db.reject_reel("idea-1") is False


@patch("lib.ideas_db.db.execute")
def test_set_reel_result_sets_only_the_passed_platform(mock_execute: MagicMock) -> None:
    mock_execute.side_effect = [1, 0]  # column update, then the social_done check misses
    ok = ideas_db.set_reel_result("idea-1", ig_reel_url="https://instagram.com/reel/1")
    assert ok is True
    first_call_query, first_call_params = mock_execute.call_args_list[0][0]
    assert "ig_reel_url = COALESCE(%s, ig_reel_url)" in first_call_query
    assert first_call_params == ("https://instagram.com/reel/1", None, "idea-1")


@patch("lib.ideas_db.db.execute")
def test_set_reel_result_flips_social_done_only_when_both_urls_present(
    mock_execute: MagicMock,
) -> None:
    mock_execute.side_effect = [1, 1]  # column update, then the social_done flip succeeds
    ideas_db.set_reel_result("idea-1", fb_reel_url="https://facebook.com/reel/1")
    second_call_query = mock_execute.call_args_list[1][0][0]
    assert "SET status = 'social_done'" in second_call_query
    assert "ig_reel_url IS NOT NULL AND fb_reel_url IS NOT NULL" in second_call_query


@patch("lib.ideas_db.db.execute")
def test_set_reel_result_catches_exception_and_returns_false(mock_execute: MagicMock) -> None:
    mock_execute.side_effect = Exception("boom")
    assert ideas_db.set_reel_result("idea-1", ig_reel_url="https://x") is False
