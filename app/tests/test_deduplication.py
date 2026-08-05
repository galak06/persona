"""Tests for deduplication.py — post dedup cache with 60-day TTL."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

import deduplication


@pytest.fixture
def tmp_cache(tmp_path):
    """Redirect dedup cache to a temp file."""
    cache_file = tmp_path / "dedup_cache.json"
    cache_file.write_text("{}")
    with patch.object(deduplication, "CACHE_FILE", cache_file):
        yield cache_file


class TestIsDuplicate:
    def test_new_post_not_duplicate(self, tmp_cache):
        assert deduplication.is_duplicate("facebook", "post_123") is False

    def test_engaged_post_is_duplicate(self, tmp_cache):
        deduplication.mark_engaged("facebook", "post_123", "comment", "Test Group")
        assert deduplication.is_duplicate("facebook", "post_123") is True

    def test_different_platform_not_duplicate(self, tmp_cache):
        deduplication.mark_engaged("facebook", "post_123", "comment")
        assert deduplication.is_duplicate("instagram", "post_123") is False

    def test_different_post_id_not_duplicate(self, tmp_cache):
        deduplication.mark_engaged("facebook", "post_123", "comment")
        assert deduplication.is_duplicate("facebook", "post_456") is False

    def test_expired_post_not_duplicate(self, tmp_cache):
        old_date = (date.today() - timedelta(days=61)).isoformat()
        cache = {
            "facebook": {
                "old_post": {
                    "engaged_at": old_date,
                    "action": "comment",
                    "status": "engaged",
                }
            }
        }
        tmp_cache.write_text(json.dumps(cache))
        assert deduplication.is_duplicate("facebook", "old_post") is False

    def test_recent_post_still_duplicate(self, tmp_cache):
        recent_date = (date.today() - timedelta(days=30)).isoformat()
        cache = {
            "facebook": {
                "recent_post": {
                    "engaged_at": recent_date,
                    "action": "comment",
                    "status": "engaged",
                }
            }
        }
        tmp_cache.write_text(json.dumps(cache))
        assert deduplication.is_duplicate("facebook", "recent_post") is True


class TestMarkEngaged:
    def test_marks_post(self, tmp_cache):
        deduplication.mark_engaged("instagram", "ig_post_1", "like", "#dogfood")
        cache = json.loads(tmp_cache.read_text())
        assert "ig_post_1" in cache["instagram"]
        entry = cache["instagram"]["ig_post_1"]
        assert entry["action"] == "like"
        assert entry["group_or_hashtag"] == "#dogfood"
        assert entry["status"] == "engaged"
        assert entry["engaged_at"] == date.today().isoformat()

    def test_failed_status(self, tmp_cache):
        deduplication.mark_engaged("facebook", "p1", "comment", status="FAILED")
        cache = json.loads(tmp_cache.read_text())
        assert cache["facebook"]["p1"]["status"] == "FAILED"

    def test_multiple_posts_same_platform(self, tmp_cache):
        deduplication.mark_engaged("facebook", "p1", "comment")
        deduplication.mark_engaged("facebook", "p2", "comment")
        cache = json.loads(tmp_cache.read_text())
        assert len(cache["facebook"]) == 2


class TestGetCacheStats:
    def test_empty_cache(self, tmp_cache):
        stats = deduplication.get_cache_stats()
        assert stats == {}

    def test_counts_per_platform(self, tmp_cache):
        deduplication.mark_engaged("facebook", "p1", "comment")
        deduplication.mark_engaged("facebook", "p2", "comment")
        deduplication.mark_engaged("instagram", "p3", "like")
        stats = deduplication.get_cache_stats()
        assert stats["facebook"] == 2
        assert stats["instagram"] == 1


class TestTTLPurge:
    def test_purges_old_entries_on_read(self, tmp_cache):
        old = (date.today() - timedelta(days=61)).isoformat()
        recent = (date.today() - timedelta(days=10)).isoformat()
        cache = {
            "facebook": {
                "old_post": {"engaged_at": old, "action": "comment", "status": "engaged"},
                "new_post": {"engaged_at": recent, "action": "comment", "status": "engaged"},
            }
        }
        tmp_cache.write_text(json.dumps(cache))
        stats = deduplication.get_cache_stats()
        assert stats["facebook"] == 1  # only new_post remains

    def test_removes_empty_platform_after_purge(self, tmp_cache):
        old = (date.today() - timedelta(days=61)).isoformat()
        cache = {
            "facebook": {
                "only_post": {"engaged_at": old, "action": "comment", "status": "engaged"},
            }
        }
        tmp_cache.write_text(json.dumps(cache))
        stats = deduplication.get_cache_stats()
        assert "facebook" not in stats

    def test_corrupted_cache_resets(self, tmp_cache):
        tmp_cache.write_text("not valid json [[[")
        assert deduplication.is_duplicate("facebook", "test") is False
