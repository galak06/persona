"""Tests for the ideas_db DB layer (local Postgres `content_ideas` table).

Per this slice's safety constraint, every `lib.db` call is mocked -- no real
Postgres connection is ever opened. This repo's pytest suite has TRUNCATEd
the live database before (see `tests/conftest.py`'s guard); these tests
never touch `DATABASE_URL` at all, mocked or otherwise required.
"""
# ruff: noqa: S101

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lib import ideas_db


def test_statuses_tuple_is_unchanged() -> None:
    """Callers (api/ideas_api.py's VALID_STATUSES) depend on this exact set/order."""
    assert ideas_db.STATUSES == (
        "publish",
        "enriching",
        "approved",
        "skipped",
        "wp_draft",
        "wp_published",
        "social_queued",
        "social_done",
        "write_failed",
        "validation_failed",
        "drafting",
        "composing_reel",
    )


@patch("lib.ideas_db.db.execute")
def test_insert_idea_builds_row_from_sheet_style_keys(mock_execute: MagicMock) -> None:
    idea_id = ideas_db.insert_idea(
        {
            "Category": "Recipes",
            "Topic": "Frozen dog treats",
            "Target_Keyword": "frozen dog treats",
            "Nalla_Context": "Nalla loves these in summer",
            "Post_Goal": "seo_traffic",
            "Input": '{"source": "manual"}',
        },
        brand_id="dogfoodandfun",
        brand_name="Dog Food & Fun",
    )
    assert idea_id is not None
    mock_execute.assert_called_once()
    query, params = mock_execute.call_args[0]
    assert "INSERT INTO content_ideas" in query
    assert params["id"] == idea_id
    assert params["category"] == "Recipes"
    assert params["topic"] == "Frozen dog treats"
    assert params["target_keyword"] == "frozen dog treats"
    assert params["nalla_context"] == "Nalla loves these in summer"
    assert params["status"] == "publish"  # default when Status absent
    assert params["brand_id"] == "dogfoodandfun"
    assert params["brand_name"] == "Dog Food & Fun"


@patch("lib.ideas_db.db.execute")
def test_insert_idea_accepts_lowercase_keys_and_omits_blank_brand(mock_execute: MagicMock) -> None:
    ideas_db.insert_idea({"category": "Health", "topic": "Grain-free diets", "status": "approved"})
    _, params = mock_execute.call_args[0]
    assert params["category"] == "Health"
    assert params["status"] == "approved"
    assert "brand_id" not in params  # not passed -> not in the INSERT column list
    assert "brand_name" not in params


@patch("lib.ideas_db.db.execute")
def test_insert_idea_catches_unique_violation_and_returns_none(mock_execute: MagicMock) -> None:
    mock_execute.side_effect = Exception(
        'duplicate key value violates unique constraint "idx_content_ideas_topic_brand"'
    )
    result = ideas_db.insert_idea({"category": "Recipes", "topic": "Duplicate topic"})
    assert result is None


@patch("lib.ideas_db.db.execute")
def test_update_status_builds_correct_update(mock_execute: MagicMock) -> None:
    ok = ideas_db.update_status("idea-1", "approved")
    assert ok is True
    query, params = mock_execute.call_args[0]
    assert "UPDATE content_ideas SET status = %s" in query
    assert "WHERE id = %s" in query
    assert params == ("approved", "idea-1")


@patch("lib.ideas_db.db.execute")
def test_update_status_catches_exception_and_returns_false(mock_execute: MagicMock) -> None:
    mock_execute.side_effect = Exception("boom")
    assert ideas_db.update_status("idea-1", "approved") is False


@patch("lib.ideas_db.db.execute")
def test_set_wp_result_builds_correct_update(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    ok = ideas_db.set_wp_result("idea-1", "42", "https://dogfoodandfun.com/post")
    assert ok is True
    query, params = mock_execute.call_args[0]
    assert "UPDATE content_ideas SET wp_post_id = %s, wp_url = %s" in query
    assert params == ("42", "https://dogfoodandfun.com/post", "idea-1")


@patch("lib.ideas_db.db.execute")
def test_set_wp_result_false_when_no_row_matched(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 0
    assert ideas_db.set_wp_result("no-such-id", "42", "https://x") is False


@patch("lib.ideas_db.db.execute")
def test_set_wp_result_catches_exception_and_returns_false(mock_execute: MagicMock) -> None:
    mock_execute.side_effect = Exception("boom")
    assert ideas_db.set_wp_result("idea-1", "42", "https://x") is False


@patch("lib.ideas_db.db.fetch_all")
def test_list_ideas_applies_status_and_brand_filters(mock_fetch_all: MagicMock) -> None:
    mock_fetch_all.return_value = [{"id": "x"}]
    rows = ideas_db.list_ideas(status="publish", brand_id="dogfoodandfun", limit=10)
    assert rows == [{"id": "x"}]
    query, params = mock_fetch_all.call_args[0]
    assert "status = %s" in query
    assert "brand_id = %s" in query
    assert "ORDER BY created_at DESC" in query
    assert params == ("publish", "dogfoodandfun", 10)


@patch("lib.ideas_db.db.fetch_all")
def test_list_ideas_unfiltered(mock_fetch_all: MagicMock) -> None:
    mock_fetch_all.return_value = []
    ideas_db.list_ideas()
    query, params = mock_fetch_all.call_args[0]
    assert query.count("%s") == 1  # just the LIMIT
    assert params == (500,)


@patch("lib.ideas_db.db.fetch_all")
def test_list_ideas_catches_exception_and_returns_empty(mock_fetch_all: MagicMock) -> None:
    mock_fetch_all.side_effect = Exception("boom")
    assert ideas_db.list_ideas() == []


@patch("lib.ideas_db.db.fetch_all")
def test_existing_topics_lowercases_and_dedupes(mock_fetch_all: MagicMock) -> None:
    mock_fetch_all.return_value = [
        {"topic": "Frozen Dog Treats"},
        {"topic": "frozen dog treats"},
        {"topic": "Grain-Free Diets"},
        {"topic": ""},
    ]
    topics = ideas_db.existing_topics(brand_id="dogfoodandfun")
    assert topics == {"frozen dog treats", "grain-free diets"}
    query, params = mock_fetch_all.call_args[0]
    assert "WHERE brand_id = %s" in query
    assert params == ("dogfoodandfun",)


@patch("lib.ideas_db.db.fetch_all")
def test_existing_topics_unfiltered_by_brand(mock_fetch_all: MagicMock) -> None:
    mock_fetch_all.return_value = [{"topic": "X"}]
    ideas_db.existing_topics()
    query, params = mock_fetch_all.call_args[0]
    assert "WHERE" not in query
    assert params is None


@patch("lib.ideas_db.db.fetch_all")
def test_existing_topics_catches_exception_and_returns_empty_set(mock_fetch_all: MagicMock) -> None:
    mock_fetch_all.side_effect = Exception("boom")
    assert ideas_db.existing_topics() == set()


@patch("lib.ideas_db.db.fetch_one")
def test_pending_count_filters_on_status_publish(mock_fetch_one: MagicMock) -> None:
    mock_fetch_one.return_value = {"n": 7}
    count = ideas_db.pending_count(brand_id="dogfoodandfun")
    assert count == 7
    query, params = mock_fetch_one.call_args[0]
    assert "status = %s" in query
    assert "brand_id = %s" in query
    assert params == ("publish", "dogfoodandfun")


@patch("lib.ideas_db.db.fetch_one")
def test_pending_count_unfiltered_by_brand(mock_fetch_one: MagicMock) -> None:
    mock_fetch_one.return_value = {"n": 3}
    count = ideas_db.pending_count()
    assert count == 3
    query, params = mock_fetch_one.call_args[0]
    assert "brand_id" not in query
    assert params == ("publish",)


@patch("lib.ideas_db.db.fetch_one")
def test_pending_count_none_row_returns_zero(mock_fetch_one: MagicMock) -> None:
    mock_fetch_one.return_value = None
    assert ideas_db.pending_count() == 0


@patch("lib.ideas_db.db.fetch_one")
def test_pending_count_catches_exception_and_returns_zero(mock_fetch_one: MagicMock) -> None:
    mock_fetch_one.side_effect = Exception("boom")
    assert ideas_db.pending_count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# get_idea


@patch("lib.ideas_db.db.fetch_one")
def test_get_idea_returns_row_when_found(mock_fetch_one: MagicMock) -> None:
    mock_fetch_one.return_value = {"id": "idea-1", "status": "approved", "topic": "Frozen treats"}
    idea = ideas_db.get_idea("idea-1")
    assert idea == {"id": "idea-1", "status": "approved", "topic": "Frozen treats"}
    query, params = mock_fetch_one.call_args[0]
    assert "SELECT * FROM content_ideas WHERE id = %s" in query
    assert params == ("idea-1",)


@patch("lib.ideas_db.db.fetch_one")
def test_get_idea_returns_none_when_not_found(mock_fetch_one: MagicMock) -> None:
    mock_fetch_one.return_value = None
    assert ideas_db.get_idea("no-such-id") is None


@patch("lib.ideas_db.db.fetch_one")
def test_get_idea_catches_exception_and_returns_none(mock_fetch_one: MagicMock) -> None:
    mock_fetch_one.side_effect = Exception("boom")
    assert ideas_db.get_idea("idea-1") is None


# ─────────────────────────────────────────────────────────────────────────────
# claim_idea_for_drafting


@patch("lib.ideas_db.db.execute")
def test_claim_idea_for_drafting_wins_claim(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 1
    assert ideas_db.claim_idea_for_drafting("idea-1") is True
    query, params = mock_execute.call_args[0]
    assert "SET status = 'drafting'" in query
    assert "WHERE id = %s AND status = 'approved'" in query
    assert params == ("idea-1",)


@patch("lib.ideas_db.db.execute")
def test_claim_idea_for_drafting_false_when_not_approved(mock_execute: MagicMock) -> None:
    mock_execute.return_value = 0
    assert ideas_db.claim_idea_for_drafting("idea-1") is False


@patch("lib.ideas_db.db.execute")
def test_claim_idea_for_drafting_catches_exception_and_returns_false(
    mock_execute: MagicMock,
) -> None:
    mock_execute.side_effect = Exception("boom")
    assert ideas_db.claim_idea_for_drafting("idea-1") is False
