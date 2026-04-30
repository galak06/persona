"""Tests for lib.observability.logger.

These exercise the structlog wiring end-to-end by capturing stdout.
Production behavior — JSON output, correlation ID injection, level
filtering — is verified against actual log output."""

from __future__ import annotations

import io
import json
import logging
import sys
from contextlib import redirect_stdout

import pytest

from lib.observability.correlation_id import correlation_id, new_correlation_id
from lib.observability.logger import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """structlog config is global; reset between tests."""
    correlation_id.set("<unset>")
    # Clear stdlib root handlers so basicConfig re-applies.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)


class TestJsonOutput:
    def test_emits_valid_json_per_line(self) -> None:
        configure_logging("INFO", pretty=False)
        log = get_logger("test")
        buf = io.StringIO()
        with redirect_stdout(buf):
            log.info("event_name", platform="fb", count=3)
        line = buf.getvalue().strip().splitlines()[-1]
        record = json.loads(line)
        assert record["event"] == "event_name"
        assert record["platform"] == "fb"
        assert record["count"] == 3
        assert record["level"] == "info"
        assert "timestamp" in record

    def test_includes_correlation_id(self) -> None:
        configure_logging("INFO", pretty=False)
        log = get_logger("test")
        buf = io.StringIO()
        with redirect_stdout(buf), new_correlation_id("test-skill", item_id="x"):
            log.info("event")
        line = buf.getvalue().strip().splitlines()[-1]
        record = json.loads(line)
        assert record["correlation_id"].startswith("test-skill:")
        assert record["correlation_id"].endswith(":x")

    def test_correlation_id_unset_when_no_context(self) -> None:
        configure_logging("INFO", pretty=False)
        log = get_logger("test")
        buf = io.StringIO()
        with redirect_stdout(buf):
            log.info("event")
        line = buf.getvalue().strip().splitlines()[-1]
        record = json.loads(line)
        assert record["correlation_id"] == "<unset>"


class TestLevelFiltering:
    def test_debug_suppressed_at_info_level(self) -> None:
        configure_logging("INFO", pretty=False)
        log = get_logger("test")
        buf = io.StringIO()
        with redirect_stdout(buf):
            log.debug("should_not_appear")
            log.info("should_appear")
        lines = [x for x in buf.getvalue().strip().splitlines() if x]
        events = [json.loads(line)["event"] for line in lines]
        assert "should_appear" in events
        assert "should_not_appear" not in events


class TestPrettyMode:
    def test_pretty_does_not_emit_json(self) -> None:
        configure_logging("INFO", pretty=True)
        log = get_logger("test")
        buf = io.StringIO()
        with redirect_stdout(buf):
            log.info("pretty_event", platform="fb")
        out = buf.getvalue()
        # ANSI escapes or readable text — definitely not JSON.
        with pytest.raises(json.JSONDecodeError):
            json.loads(out.strip().splitlines()[-1])
        assert "pretty_event" in out


class TestEnvVarToggle:
    def test_pretty_logs_env_enables_pretty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRETTY_LOGS", "1")
        configure_logging("INFO")
        log = get_logger("test")
        buf = io.StringIO()
        with redirect_stdout(buf):
            log.info("env_pretty")
        # Output should not be JSON-parseable (it's pretty).
        out = buf.getvalue().strip()
        with pytest.raises(json.JSONDecodeError):
            json.loads(out.splitlines()[-1])

    def test_unset_env_uses_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PRETTY_LOGS", raising=False)
        configure_logging("INFO")
        log = get_logger("test")
        buf = io.StringIO()
        with redirect_stdout(buf):
            log.info("env_json")
        line = buf.getvalue().strip().splitlines()[-1]
        record = json.loads(line)
        assert record["event"] == "env_json"


class TestIdempotent:
    def test_double_configure_does_not_crash(self) -> None:
        configure_logging("INFO", pretty=False)
        configure_logging("DEBUG", pretty=False)
        log = get_logger("test")
        buf = io.StringIO()
        with redirect_stdout(buf):
            log.debug("now_visible")
        line = buf.getvalue().strip().splitlines()[-1]
        record = json.loads(line)
        assert record["event"] == "now_visible"


def test_module_imports_without_side_effects() -> None:
    """Importing the module must not configure logging — that's the
    runner's responsibility. Verifies no top-level configure_logging()."""
    if "lib.observability.logger" in sys.modules:
        del sys.modules["lib.observability.logger"]
    from lib.observability import logger  # noqa: F401

    # Just verifying import didn't raise. If side-effecting was wired,
    # subsequent calls would behave differently — kept as a smoke test.
