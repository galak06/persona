"""Tests for the published_content DB layer.

Per this slice's safety constraint, every `lib.db` call is mocked -- no real
Postgres connection is ever opened. This repo's pytest suite has TRUNCATEd
the live database before (see `tests/conftest.py`'s guard); these tests
never touch `DATABASE_URL` at all, mocked or otherwise required.
"""
# ruff: noqa: S101

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lib.published_content_db.models import dedup_id
from lib.published_content_db.repository import PublishedContentRepository


def test_dedup_id_is_brand_scoped() -> None:
    """Unlike engagements_db's dedup_id, brand_id IS part of the key -- two
    brands ranking for the identical query text against the same-shaped URL
    must not collide on one shared table."""
    a = dedup_id("brandA", "https://example.com/post", "dog food")
    b = dedup_id("brandB", "https://example.com/post", "dog food")
    assert a != b


def test_dedup_id_is_stable() -> None:
    first = dedup_id("dogfoodandfun", "https://dogfoodandfun.com/post", "homemade dog treats")
    second = dedup_id("dogfoodandfun", "https://dogfoodandfun.com/post", "homemade dog treats")
    assert first == second


def test_upsert_requires_brand_url_and_query() -> None:
    repo = PublishedContentRepository()
    with pytest.raises(ValueError):
        repo.upsert({"wp_url": "u", "gsc_query": "q"})
    with pytest.raises(ValueError):
        repo.upsert({"brand_id": "b", "gsc_query": "q"})
    with pytest.raises(ValueError):
        repo.upsert({"brand_id": "b", "wp_url": "u"})


@patch("lib.published_content_db.repository.db.execute")
def test_upsert_builds_on_conflict_upsert(mock_execute: MagicMock) -> None:
    repo = PublishedContentRepository()
    row_id = repo.upsert(
        {
            "brand_id": "dogfoodandfun",
            "wp_url": "https://dogfoodandfun.com/post",
            "gsc_query": "homemade dog treats",
            "position": 8.4,
            "impressions": 120,
            "clicks": 6,
            "ctr": 0.05,
        }
    )
    assert row_id == dedup_id(
        "dogfoodandfun", "https://dogfoodandfun.com/post", "homemade dog treats"
    )
    mock_execute.assert_called_once()
    query, params = mock_execute.call_args[0]
    assert "INSERT INTO published_content" in query
    assert "ON CONFLICT (id) DO UPDATE SET" in query
    assert "id = EXCLUDED.id" not in query  # id must never be in the UPDATE SET clause
    assert params["brand_id"] == "dogfoodandfun"
    assert params["position"] == 8.4
    assert params["impressions"] == 120
    # created_at has no row key of its own -- the query reuses %(updated_at)s
    # for it, so a fresh insert's created_at == updated_at at that moment.
    assert "created_at" not in params
    assert "created_at" in query


def test_upsert_defaults_numeric_and_optional_fields() -> None:
    with patch("lib.published_content_db.repository.db.execute") as mock_execute:
        repo = PublishedContentRepository()
        repo.upsert({"brand_id": "b", "wp_url": "u", "gsc_query": "q"})
        _, params = mock_execute.call_args[0]
    assert params["position"] == 0.0
    assert params["impressions"] == 0
    assert params["clicks"] == 0
    assert params["ctr"] == 0.0
    assert params["wp_post_id"] == ""


@patch("lib.published_content_db.repository.db.fetch_all")
def test_list_for_brand_applies_all_filters(mock_fetch_all: MagicMock) -> None:
    mock_fetch_all.return_value = [{"id": "x"}]
    repo = PublishedContentRepository()
    rows = repo.list_for_brand(
        "dogfoodandfun",
        wp_url="https://dogfoodandfun.com/post",
        min_position=6,
        max_position=25,
        min_impressions=40,
        limit=50,
    )
    assert rows == [{"id": "x"}]
    query, params = mock_fetch_all.call_args[0]
    assert "brand_id = %s" in query
    assert "wp_url = %s" in query
    assert "position >= %s" in query
    assert "position <= %s" in query
    assert "impressions >= %s" in query
    assert params[0] == "dogfoodandfun"
    assert params[-1] == 50  # limit is always the last bound param


@patch("lib.published_content_db.repository.db.fetch_all")
def test_list_for_brand_unfiltered(mock_fetch_all: MagicMock) -> None:
    mock_fetch_all.return_value = []
    repo = PublishedContentRepository()
    repo.list_for_brand("dogfoodandfun")
    query, params = mock_fetch_all.call_args[0]
    assert query.count("%s") == 2  # just brand_id + limit
    assert params == ("dogfoodandfun", 1000)


@patch("lib.published_content_db.repository.db.fetch_one")
def test_get_delegates_to_fetch_one(mock_fetch_one: MagicMock) -> None:
    mock_fetch_one.return_value = {"id": "x"}
    repo = PublishedContentRepository()
    assert repo.get("x") == {"id": "x"}
    mock_fetch_one.assert_called_once_with("SELECT * FROM published_content WHERE id = %s", ("x",))
