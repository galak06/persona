"""Tests for lib.crew.products.usage's append-only JSONL usage tracker.

No mocking -- every test passes an explicit tmp_path file, exercising the
real append/read code (the `BRAND_DIR` default-path branch is bypassed by
design, same as other state-file tests).
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lib.crew.products.usage import recently_used_keys, record_usage


def _write_line(path: Path, ts: datetime, idea_id: str, keys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": ts.isoformat(), "idea_id": idea_id, "keys": keys}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def test_record_then_read_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state" / "product_usage.jsonl"
    record_usage("idea-1", ["fi-collar", "treat-jar"], path=path)
    record_usage("idea-2", ["sgoda-cooling-vest"], path=path)
    assert recently_used_keys(path=path) == {"fi-collar", "treat-jar", "sgoda-cooling-vest"}
    # Append-only: two records -> exactly two lines, first one intact.
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["idea_id"] == "idea-1"
    assert first["keys"] == ["fi-collar", "treat-jar"]


def test_ttl_window_excludes_old_entries(tmp_path: Path) -> None:
    path = tmp_path / "product_usage.jsonl"
    now = datetime.now(UTC)
    _write_line(path, now - timedelta(days=40), "idea-old", ["stale-key"])
    _write_line(path, now - timedelta(days=1), "idea-new", ["fresh-key"])
    assert recently_used_keys(window_days=30, path=path) == {"fresh-key"}
    # A wider window brings the old entry back -- read-side filter only.
    assert recently_used_keys(window_days=60, path=path) == {"stale-key", "fresh-key"}


def test_missing_file_returns_empty_set(tmp_path: Path) -> None:
    assert recently_used_keys(path=tmp_path / "nope.jsonl") == set()


def test_corrupt_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "product_usage.jsonl"
    _write_line(path, datetime.now(UTC), "idea-1", ["good-key"])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")
        handle.write('"a bare string, not an object"\n')
    assert recently_used_keys(path=path) == {"good-key"}
