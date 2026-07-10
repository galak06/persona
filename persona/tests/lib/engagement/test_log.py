"""Tests for lib.engagement.log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.engagement.log import log_engagement


@pytest.fixture
def log_file(tmp_path: Path) -> Path:
    return tmp_path / "logs/engagement_log.jsonl"


class TestLogEngagement:
    def test_creates_parent_dir(self, log_file: Path) -> None:
        log_engagement("comment", "facebook", "Test Group", "draft", log_file=log_file)
        assert log_file.exists()

    def test_writes_one_line_per_call(self, log_file: Path) -> None:
        log_engagement("comment", "facebook", "Group A", "first", log_file=log_file)
        log_engagement("like", "instagram", "#hashtag", "second", log_file=log_file)
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_each_line_is_valid_json(self, log_file: Path) -> None:
        log_engagement("comment", "facebook", "Group", "draft", log_file=log_file)
        line = log_file.read_text().strip()
        record = json.loads(line)
        assert record["action"] == "comment"
        assert record["platform"] == "facebook"
        assert record["target_name"] == "Group"
        assert record["content"] == "draft"

    def test_record_includes_required_fields(self, log_file: Path) -> None:
        log_engagement("comment", "facebook", "Group", "x", log_file=log_file)
        record = json.loads(log_file.read_text().strip())
        for field in ("date", "timestamp", "action", "platform", "target_name", "content"):
            assert field in record

    def test_long_content_truncated_to_200_chars(self, log_file: Path) -> None:
        big = "x" * 500
        log_engagement("comment", "facebook", "Group", big, log_file=log_file)
        record = json.loads(log_file.read_text().strip())
        assert len(record["content"]) == 200

    def test_short_content_preserved(self, log_file: Path) -> None:
        log_engagement("comment", "facebook", "Group", "short", log_file=log_file)
        record = json.loads(log_file.read_text().strip())
        assert record["content"] == "short"

    def test_unicode_round_trips(self, log_file: Path) -> None:
        log_engagement("comment", "facebook", "Group", "Nalla — fluffy", log_file=log_file)
        record = json.loads(log_file.read_text().strip())
        assert record["content"] == "Nalla — fluffy"

    def test_appends_not_truncates(self, log_file: Path) -> None:
        log_engagement("comment", "facebook", "Group A", "first", log_file=log_file)
        log_engagement("comment", "facebook", "Group A", "second", log_file=log_file)
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["content"] == "first"
        assert json.loads(lines[1])["content"] == "second"
