"""Tests for logger.py — timestamped output formatting."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from logger import StepTimer, log, log_error, log_progress, log_step, log_warn


class TestLogOutput:
    def test_log_includes_timestamp(self, capsys):
        log("test message")
        out = capsys.readouterr().out
        assert "INFO: test message" in out
        assert "]" in out  # timestamp bracket

    def test_log_level(self, capsys):
        log("warning here", level="WARN")
        out = capsys.readouterr().out
        assert "WARN: warning here" in out

    def test_log_step(self, capsys):
        log_step("Loading data", "from cache")
        out = capsys.readouterr().out
        assert ">> Loading data" in out
        assert "from cache" in out

    def test_log_progress(self, capsys):
        log_progress(3, 7, "Scanning group", "homemade food")
        out = capsys.readouterr().out
        assert "[3/7]" in out
        assert "Scanning group" in out

    def test_log_warn(self, capsys):
        log_warn("disk full")
        out = capsys.readouterr().out
        assert "WARN: disk full" in out

    def test_log_error(self, capsys):
        log_error("connection failed")
        out = capsys.readouterr().out
        assert "ERROR: connection failed" in out


class TestStepTimer:
    def test_timer_logs_start_and_end(self, capsys):
        with StepTimer("test step"):
            pass
        out = capsys.readouterr().out
        assert "test step" in out
        assert "started" in out
        assert "done" in out

    def test_timer_reports_elapsed(self, capsys):
        import time

        with StepTimer("quick step"):
            time.sleep(0.1)
        out = capsys.readouterr().out
        assert "s)" in out  # elapsed time in seconds
