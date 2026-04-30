"""Tests for lib.scheduling.once_per_window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lib.scheduling import (
    AlreadyRanError,
    last_run_status,
    once_per,
    record_run,
)


@pytest.fixture
def last_run_file(tmp_path: Path) -> Path:
    return tmp_path / "last_run.json"


class TestRecordRun:
    def test_writes_new_entry(self, last_run_file: Path) -> None:
        record_run("a", last_run_file=last_run_file)
        rec = last_run_status("a", last_run_file=last_run_file)
        assert rec is not None
        assert rec["status"] == "success"
        assert "last_run_at" in rec

    def test_preserves_other_skills(self, last_run_file: Path) -> None:
        record_run("a", last_run_file=last_run_file)
        record_run("b", last_run_file=last_run_file)
        assert last_run_status("a", last_run_file=last_run_file) is not None
        assert last_run_status("b", last_run_file=last_run_file) is not None

    def test_overwrites_same_skill(self, last_run_file: Path) -> None:
        record_run("a", status="failed", last_run_file=last_run_file)
        record_run("a", status="success", last_run_file=last_run_file)
        rec = last_run_status("a", last_run_file=last_run_file)
        assert rec is not None
        assert rec["status"] == "success"

    def test_extra_fields_persisted(self, last_run_file: Path) -> None:
        record_run(
            "a",
            extra={"posted": 5, "skipped": 2},
            last_run_file=last_run_file,
        )
        rec = last_run_status("a", last_run_file=last_run_file)
        assert rec is not None
        assert rec.get("posted") == 5  # type: ignore[typeddict-item]
        assert rec.get("skipped") == 2  # type: ignore[typeddict-item]

    def test_timestamp_format_iso_with_z(self, last_run_file: Path) -> None:
        when = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
        record_run("a", when=when, last_run_file=last_run_file)
        rec = last_run_status("a", last_run_file=last_run_file)
        assert rec is not None
        assert rec["last_run_at"] == "2026-04-30T12:00:00Z"


class TestLastRunStatus:
    def test_returns_none_when_file_missing(self, last_run_file: Path) -> None:
        assert last_run_status("never", last_run_file=last_run_file) is None

    def test_returns_none_when_skill_missing(self, last_run_file: Path) -> None:
        record_run("other", last_run_file=last_run_file)
        assert last_run_status("never", last_run_file=last_run_file) is None


class TestOncePerSkip:
    def test_skips_when_recent_success(self, last_run_file: Path) -> None:
        when = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
        record_run("comment-poster", when=when, last_run_file=last_run_file)
        # Same day — within 24h window.
        same_day = when + timedelta(hours=2)
        with (
            pytest.raises(AlreadyRanError) as exc,
            once_per(
                "comment-poster",
                hours=24,
                last_run_file=last_run_file,
                now=same_day,
            ),
        ):
            pass
        assert exc.value.context.get("skill") == "comment-poster"
        assert exc.value.context.get("window_hours") == 24


class TestOncePerRun:
    def test_runs_when_no_prior(self, last_run_file: Path) -> None:
        executed = False
        with once_per("first-run", hours=24, last_run_file=last_run_file):
            executed = True
        assert executed is True

    def test_runs_when_prior_failed(self, last_run_file: Path) -> None:
        when = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
        record_run("flaky", status="failed", when=when, last_run_file=last_run_file)
        executed = False
        with once_per(
            "flaky",
            hours=24,
            last_run_file=last_run_file,
            now=when + timedelta(hours=1),
        ):
            executed = True
        assert executed is True

    def test_runs_when_outside_window(self, last_run_file: Path) -> None:
        when = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
        record_run("stale", when=when, last_run_file=last_run_file)
        next_day = when + timedelta(hours=25)
        executed = False
        with once_per("stale", hours=24, last_run_file=last_run_file, now=next_day):
            executed = True
        assert executed is True

    def test_force_bypasses_guard(self, last_run_file: Path) -> None:
        when = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
        record_run("forceable", when=when, last_run_file=last_run_file)
        executed = False
        with once_per(
            "forceable",
            hours=24,
            force=True,
            last_run_file=last_run_file,
            now=when + timedelta(hours=2),
        ):
            executed = True
        assert executed is True

    def test_malformed_timestamp_does_not_lock_out(self, last_run_file: Path) -> None:
        """Bad data in last_run.json must not gate a runner forever — bias to running."""
        last_run_file.write_text(
            '{"broken": {"last_run_at": "not-a-timestamp", "status": "success"}}',
            encoding="utf-8",
        )
        executed = False
        with once_per("broken", hours=24, last_run_file=last_run_file):
            executed = True
        assert executed is True


class TestOncePerCustomWindows:
    def test_seven_day_window_blocks_within(self, last_run_file: Path) -> None:
        when = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
        record_run("weekly", when=when, last_run_file=last_run_file)
        with (
            pytest.raises(AlreadyRanError),
            once_per(
                "weekly",
                hours=7 * 24,
                last_run_file=last_run_file,
                now=when + timedelta(days=3),
            ),
        ):
            pass

    def test_seven_day_window_allows_after(self, last_run_file: Path) -> None:
        when = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
        record_run("weekly", when=when, last_run_file=last_run_file)
        executed = False
        with once_per(
            "weekly",
            hours=7 * 24,
            last_run_file=last_run_file,
            now=when + timedelta(days=8),
        ):
            executed = True
        assert executed is True
