"""Tests for `lib/activity_log.py`'s path resolution and `log_trace()`.

Regression coverage for a live-discovered bug: `ENGAGEMENT_LOG_PATH` used
to be hardcoded relative to this repo's own root instead of `BRAND_DIR`,
so in the Docker worker container the directory never existed and every
`log_trace()` call (hit at the start of ig_engager.py/fb_scan.py) crashed
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


def _write_log(target: Path, rows: list[dict[str, str]]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _row(date: str, action: str, platform: str = "facebook") -> dict[str, str]:
    return {
        "date": date,
        "timestamp": f"{date}T12:00:00+00:00",
        "action": action,
        "platform": platform,
    }


def test_summarize_day_counts_only_the_requested_date(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "logs" / "engagement_log.jsonl"
    _write_log(
        target,
        [
            _row("2026-08-13", "comment"),
            _row("2026-08-14", "comment"),
            _row("2026-08-14", "comment"),
            _row("2026-08-14", "group_join"),
            _row("2026-08-15", "like", "instagram"),
        ],
    )
    monkeypatch.setattr(activity_log, "ENGAGEMENT_LOG_PATH", target)

    counts, total = activity_log.summarize_day("2026-08-14")

    assert counts["comment"] == 2
    assert counts["group_join"] == 1
    assert counts["like"] == 0
    assert total == 3


def test_summarize_day_survives_a_trace_flood(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bug this endpoint exists for.

    The Dashboard used to tail 200 rows and tally client-side. Hourly
    ``trace`` heartbeats crowd out real actions in that window, so the
    real comments/joins scrolled off the end and showed as zero. Summing
    the whole file must still find them behind 500 traces.
    """
    target = tmp_path / "logs" / "engagement_log.jsonl"
    rows = [_row("2026-08-14", "comment"), _row("2026-08-14", "group_join")]
    rows += [_row("2026-08-14", "trace", "system") for _ in range(500)]
    _write_log(target, rows)
    monkeypatch.setattr(activity_log, "ENGAGEMENT_LOG_PATH", target)

    counts, total = activity_log.summarize_day("2026-08-14")

    assert counts["comment"] == 1
    assert counts["group_join"] == 1
    assert counts["trace"] == 500
    # ``trace`` is a heartbeat, not activity — it must not inflate the total.
    assert total == 2


def test_summarize_day_returns_every_action_key_for_an_empty_day(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "logs" / "engagement_log.jsonl"
    _write_log(target, [_row("2026-08-13", "comment")])
    monkeypatch.setattr(activity_log, "ENGAGEMENT_LOG_PATH", target)

    counts, total = activity_log.summarize_day("2026-08-14")

    assert total == 0
    assert set(counts) == set(activity_log.VALID_ACTIONS)
    assert all(v == 0 for v in counts.values())
