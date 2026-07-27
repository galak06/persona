"""Tests for `lib/activity_log.py`'s path resolution and `log_trace()`.

Regression coverage for a live-discovered bug: `ENGAGEMENT_LOG_PATH` used
to be hardcoded relative to this repo's own root instead of `BRAND_DIR`,
so in the Docker worker container the directory never existed and every
`log_trace()` call (hit at the start of ig_scan.py/fb_scan.py) crashed
with `FileNotFoundError`, failing the whole flow run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import activity_log
from lib.config import settings


def test_engagement_log_path_resolves_under_brand_dir_logs() -> None:
    assert settings.paths is not None
    assert activity_log.ENGAGEMENT_LOG_PATH == settings.paths.logs_dir / "engagement_log.jsonl"


def test_log_trace_creates_missing_parent_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "logs" / "engagement_log.jsonl"
    assert not target.parent.exists()
    monkeypatch.setattr(activity_log, "ENGAGEMENT_LOG_PATH", target)

    activity_log.log_trace("instagram", "Started Instagram hashtag scan")

    assert target.exists()
    row = json.loads(target.read_text(encoding="utf-8").strip())
    assert row["action"] == "trace"
    assert row["platform"] == "instagram"
    assert row["content"] == "Started Instagram hashtag scan"


def test_log_trace_appends_without_clobbering_existing_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "logs" / "engagement_log.jsonl"
    monkeypatch.setattr(activity_log, "ENGAGEMENT_LOG_PATH", target)

    activity_log.log_trace("facebook", "first")
    activity_log.log_trace("facebook", "second")

    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["content"] == "first"
    assert json.loads(lines[1])["content"] == "second"
