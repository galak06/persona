"""Tests for lib.engagement.history."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from lib.engagement.history import (
    DEFAULT_ENGAGEMENT_ACTIONS,
    posted_targets,
    template_usage,
)


@pytest.fixture
def log_file(tmp_path: Path) -> Path:
    return tmp_path / "engagement.jsonl"


def _write_log(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


class TestPostedTargetsDefault:
    def test_empty_when_file_missing(self, log_file: Path) -> None:
        assert posted_targets(log_file=log_file) == set()

    def test_default_filter_includes_comment_and_like(self, log_file: Path) -> None:
        _write_log(
            log_file,
            [
                {"action": "comment", "target_name": "Group A", "date": "2026-04-30"},
                {"action": "like", "target_name": "Group B", "date": "2026-04-30"},
                {"action": "group_post", "target_name": "Group C", "date": "2026-04-30"},
                {"action": "own_reply", "target_name": "Group D", "date": "2026-04-30"},
            ],
        )
        result = posted_targets(log_file=log_file)
        assert result == {"Group A", "Group B"}

    def test_default_filter_excludes_group_post(self, log_file: Path) -> None:
        """The drift fix — group_post is broadcast, not engagement."""
        _write_log(
            log_file,
            [{"action": "group_post", "target_name": "Broadcast Group", "date": "2026-04-30"}],
        )
        assert "Broadcast Group" not in posted_targets(log_file=log_file)


class TestPostedTargetsCustomFilter:
    def test_custom_action_set(self, log_file: Path) -> None:
        _write_log(
            log_file,
            [
                {"action": "comment", "target_name": "A", "date": "2026-04-30"},
                {"action": "group_post", "target_name": "B", "date": "2026-04-30"},
            ],
        )
        result = posted_targets(actions=frozenset({"group_post"}), log_file=log_file)
        assert result == {"B"}

    def test_empty_filter_includes_everything(self, log_file: Path) -> None:
        _write_log(
            log_file,
            [
                {"action": "comment", "target_name": "A", "date": "2026-04-30"},
                {"action": "group_post", "target_name": "B", "date": "2026-04-30"},
                {"action": "own_reply", "target_name": "C", "date": "2026-04-30"},
            ],
        )
        result = posted_targets(actions=frozenset(), log_file=log_file)
        assert result == {"A", "B", "C"}


class TestPostedTargetsRobustness:
    def test_skips_malformed_lines(self, log_file: Path) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(
            '{"action": "comment", "target_name": "Good", "date": "2026-04-30"}\n'
            "this is not json\n"
            '{"action": "comment", "target_name": "Also Good", "date": "2026-04-30"}\n',
            encoding="utf-8",
        )
        result = posted_targets(log_file=log_file)
        assert result == {"Good", "Also Good"}

    def test_skips_records_without_target(self, log_file: Path) -> None:
        _write_log(
            log_file,
            [
                {"action": "comment", "date": "2026-04-30"},
                {"action": "comment", "target_name": "", "date": "2026-04-30"},
                {"action": "comment", "target_name": "Real", "date": "2026-04-30"},
            ],
        )
        assert posted_targets(log_file=log_file) == {"Real"}


class TestDefaultActions:
    def test_canonical_set(self) -> None:
        assert DEFAULT_ENGAGEMENT_ACTIONS == frozenset({"comment", "like"})


class TestTemplateUsageDefault:
    def test_empty_when_file_missing(self, log_file: Path) -> None:
        assert template_usage(log_file=log_file) == {}

    def test_records_recent_template(self, log_file: Path) -> None:
        today = date(2026, 4, 30)
        _write_log(
            log_file,
            [
                {
                    "action": "comment",
                    "target_name": "Group A",
                    "content": "We tried this with Nalla and it really worked great",
                    "date": "2026-04-29",
                }
            ],
        )
        usage = template_usage(log_file=log_file, today=today)
        assert "Group A" in usage
        # Default truncation is 40 chars
        assert any(k.startswith("We tried this") for k in usage["Group A"])

    def test_excludes_outside_window(self, log_file: Path) -> None:
        today = date(2026, 4, 30)
        old = (today - timedelta(days=45)).isoformat()
        _write_log(
            log_file,
            [
                {
                    "action": "comment",
                    "target_name": "Group A",
                    "content": "old comment",
                    "date": old,
                }
            ],
        )
        assert template_usage(log_file=log_file, today=today, window_days=30) == {}

    def test_keeps_most_recent_date_per_snippet(self, log_file: Path) -> None:
        today = date(2026, 4, 30)
        _write_log(
            log_file,
            [
                {
                    "action": "comment",
                    "target_name": "Group A",
                    "content": "Identical template start here",
                    "date": "2026-04-15",
                },
                {
                    "action": "comment",
                    "target_name": "Group A",
                    "content": "Identical template start here",
                    "date": "2026-04-25",
                },
            ],
        )
        usage = template_usage(log_file=log_file, today=today)
        snippet = "Identical template start here"[:40]
        assert usage["Group A"][snippet] == date(2026, 4, 25)

    def test_only_comment_actions_count(self, log_file: Path) -> None:
        today = date(2026, 4, 30)
        _write_log(
            log_file,
            [
                {
                    "action": "like",
                    "target_name": "Group A",
                    "content": "irrelevant",
                    "date": "2026-04-29",
                }
            ],
        )
        assert template_usage(log_file=log_file, today=today) == {}

    def test_custom_snippet_chars(self, log_file: Path) -> None:
        today = date(2026, 4, 30)
        _write_log(
            log_file,
            [
                {
                    "action": "comment",
                    "target_name": "G",
                    "content": "x" * 100,
                    "date": "2026-04-29",
                }
            ],
        )
        usage = template_usage(log_file=log_file, today=today, snippet_chars=10)
        # The snippet should be exactly 10 chars
        assert any(len(k) == 10 for k in usage["G"])
