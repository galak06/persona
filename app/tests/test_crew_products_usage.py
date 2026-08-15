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

from lib.crew.products.usage import (
    SOURCE_BACKFILL,
    SOURCE_DRAFT,
    SOURCE_LEGACY,
    recently_used_keys,
    record_usage,
)


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


# ── source scoping ───────────────────────────────────────────────────────────


def test_a_backfill_sweep_does_not_gate_the_drafting_path(tmp_path: Path) -> None:
    """The live failure: a sweep stamped 59 products as used, and the next
    drafted post could not feature the very product it was about."""
    path = tmp_path / "product_usage.jsonl"
    record_usage("old-published-post", ["fi-collar"], path=path, source=SOURCE_BACKFILL)

    assert recently_used_keys(path=path, sources={SOURCE_DRAFT}) == set()
    assert recently_used_keys(path=path) == {"fi-collar"}  # unscoped still sees it


def test_the_drafting_path_still_dedupes_against_itself(tmp_path: Path) -> None:
    """Scoping must not disable dedupe -- only narrow whose history counts."""
    path = tmp_path / "product_usage.jsonl"
    record_usage("a-drafted-post", ["fi-collar"], path=path)

    assert recently_used_keys(path=path, sources={SOURCE_DRAFT}) == {"fi-collar"}


def test_records_written_before_source_existed_are_legacy(tmp_path: Path) -> None:
    """The 63 pre-existing events carry no `source`; unattributable bulk
    history must not gate new work."""
    path = tmp_path / "product_usage.jsonl"
    path.write_text(
        json.dumps({"ts": datetime.now(UTC).isoformat(), "idea_id": "x", "keys": ["fi-collar"]})
        + "\n",
        encoding="utf-8",
    )

    assert recently_used_keys(path=path, sources={SOURCE_DRAFT}) == set()
    assert recently_used_keys(path=path, sources={SOURCE_LEGACY}) == {"fi-collar"}


def test_source_defaults_to_draft(tmp_path: Path) -> None:
    path = tmp_path / "product_usage.jsonl"
    record_usage("a-post", ["fi-collar"], path=path)

    row = json.loads(path.read_text().splitlines()[0])
    assert row["source"] == SOURCE_DRAFT
