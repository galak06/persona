"""Tests for lib.io.jsonio — atomic JSON read/write."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.io.jsonio import read_json, write_json


class TestReadJson:
    def test_returns_default_when_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.json"
        assert read_json(path, default={}) == {}
        assert read_json(path, default=[]) == []
        assert read_json(path, default=None) is None

    def test_reads_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        path.write_text('{"a": 1, "b": [2, 3]}', encoding="utf-8")
        assert read_json(path, default={}) == {"a": 1, "b": [2, 3]}

    def test_propagates_parse_errors(self, tmp_path: Path) -> None:
        """Malformed JSON must raise — silent fallback to default would
        mask data corruption."""
        path = tmp_path / "bad.json"
        path.write_text("not valid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            read_json(path, default={})

    def test_handles_unicode(self, tmp_path: Path) -> None:
        path = tmp_path / "unicode.json"
        path.write_text('{"name": "Nalla — fluffy"}', encoding="utf-8")
        assert read_json(path, default={}) == {"name": "Nalla — fluffy"}


class TestWriteJsonAtomic:
    def test_writes_basic_value(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        write_json(path, {"a": 1})
        assert json.loads(path.read_text()) == {"a": 1}

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        path = tmp_path / "new" / "deep" / "out.json"
        write_json(path, [1, 2, 3])
        assert json.loads(path.read_text()) == [1, 2, 3]

    def test_overwrite_replaces_atomically(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        write_json(path, {"v": 1})
        write_json(path, {"v": 2})
        assert json.loads(path.read_text()) == {"v": 2}

    def test_indent_default_is_2(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        write_json(path, {"a": 1, "b": {"c": 2}})
        # Two-space indent is visible in the rendered text.
        assert '  "a": 1' in path.read_text()

    def test_indent_override(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        write_json(path, {"a": 1}, indent=4)
        assert '    "a": 1' in path.read_text()

    def test_unicode_preserved(self, tmp_path: Path) -> None:
        path = tmp_path / "unicode.json"
        write_json(path, {"name": "Nalla — fluffy"})
        # ensure_ascii=False; the en-dash is in the file as bytes.
        assert "Nalla — fluffy" in path.read_text(encoding="utf-8")

    def test_no_temp_files_left_after_success(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        write_json(path, {"a": 1})
        # Only the target file should exist; temp files (.out.json.*.tmp) gone.
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0] == path


class TestWriteJsonAtomicCleanup:
    def test_temp_file_cleaned_on_failure(self, tmp_path: Path) -> None:
        """If os.replace fails, the temp file must not be left behind."""
        path = tmp_path / "out.json"

        # Force os.replace to raise after the temp file is written.
        original_replace = os.replace

        def failing_replace(_src: object, _dst: object) -> None:
            raise OSError("simulated replace failure")

        with (
            patch("lib.io.jsonio.os.replace", side_effect=failing_replace),
            pytest.raises(OSError, match="simulated"),
        ):
            write_json(path, {"a": 1})

        # Target wasn't created; temp file was cleaned up.
        assert not path.exists()
        leftovers = list(tmp_path.iterdir())
        assert leftovers == [], f"unexpected leftover files: {leftovers}"

        # Sanity: original os.replace still works after patch context.
        assert original_replace is os.replace


class TestWriteJsonNonAtomic:
    def test_non_atomic_skips_temp(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        write_json(path, {"a": 1}, atomic=False)
        assert json.loads(path.read_text()) == {"a": 1}


class TestConcurrency:
    def test_atomic_write_visible_or_invisible_never_partial(self, tmp_path: Path) -> None:
        """Reader sees either the previous or new content — never a half-write.

        Stress test: writer thread keeps overwriting, reader thread reads
        in a loop. Every read either parses cleanly OR returns the old
        value. JSON parse errors would indicate a corrupted half-write.
        """
        path = tmp_path / "data.json"
        write_json(path, {"v": 0})

        stop = threading.Event()
        errors: list[str] = []

        def writer() -> None:
            for i in range(200):
                if stop.is_set():
                    return
                write_json(path, {"v": i})

        def reader() -> None:
            while not stop.is_set():
                try:
                    read_json(path, default=None)
                except json.JSONDecodeError as e:
                    errors.append(str(e))
                    return

        w = threading.Thread(target=writer)
        r = threading.Thread(target=reader)
        w.start()
        r.start()
        w.join(timeout=10)
        stop.set()
        r.join(timeout=2)

        assert errors == [], f"reader observed half-written file(s): {errors}"
