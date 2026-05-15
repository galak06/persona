"""Atomic JSON file IO.

`read_json` and `write_json` replace 9 inline reimplementations across
`scripts/*.py`. Atomic-write (temp file + `os.replace`) is the default
because every state file we write — comment_queue, dedup_cache,
last_run, groups_tracker — must never be corrupted by a crash mid-write.

Production rule: never use `json.dump(open(...))` directly. Use these.
"""

from __future__ import annotations

import json
import os
import tempfile
import fcntl
import contextlib
from pathlib import Path
from typing import Generator, TypeVar

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

T = TypeVar("T")

@contextlib.contextmanager
def locked_json(path: Path, default: T, indent: int = 2) -> Generator[T, None, None]:
    """Context manager for atomic read-modify-write loops under an OS lock.

    Locks the target file, reads and yields the JSON content. When the block
    exits, writes the modified object back to disk atomically (temp + replace).
    This serializes concurrent modifications from multiple processes.
    
    Args:
        path: The JSON file to lock and modify.
        default: The default value to yield if the file is missing or empty.
        indent: JSON pretty-print indent. Default 2.
    """
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(default), encoding="utf-8")

    with path.open("r+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                raw = ""
            
            try:
                data = json.loads(raw) if raw.strip() else default
            except json.JSONDecodeError:
                data = default
                
            yield data
            
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(data, indent=indent, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp_path, path)
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
"""Recursive type for any JSON-parseable value. Use as the return type
of `read_json` when the caller is happy to refine via `cast` or
`isinstance` at the use site."""


def read_json(path: Path, default: JsonValue) -> JsonValue:
    """Read JSON from `path`, returning `default` if the file does not exist.

    Args:
        path: File to read. May be missing — returns `default` in that case.
        default: Value to return if `path` doesn't exist.

    Returns:
        Parsed JSON or `default`. Never raises `FileNotFoundError`.

    Raises:
        json.JSONDecodeError: If the file exists but is not valid JSON.
            Surfacing parse errors is intentional — silent fallback to
            `default` would mask data corruption.
    """
    if not path.exists():
        return default
    parsed: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    return parsed


def write_json(path: Path, data: object, *, atomic: bool = True, indent: int = 2) -> None:
    """Write `data` to `path` as JSON.

    Atomic by default: writes to a temp file in the same directory then
    `os.replace`s onto the target. The replace is atomic on POSIX —
    readers see either the old contents or the new, never a half-written
    file. The caller can opt out (`atomic=False`) for cases where
    durability isn't worth the extra inode churn (test artifacts, etc.),
    but production code should always use the default.

    Args:
        path: Target file. Parent directory created if missing.
        data: JSON-serializable value.
        atomic: Use temp-file + replace pattern. Default True.
        indent: JSON pretty-print indent. Default 2 to match the
            existing convention across scripts/.

    Raises:
        OSError: On filesystem failures (no space, permissions, etc.).
            These are not caught — write failures must propagate so the
            caller can decide whether to retry or escalate.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=indent, ensure_ascii=False)

    if not atomic:
        path.write_text(serialized, encoding="utf-8")
        return

    # Same-directory temp file so os.replace is a real atomic rename
    # (cross-filesystem replaces fall back to copy + unlink, defeating
    # the atomicity).
    fd, tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
        os.replace(tmp_path, path)
    except BaseException:
        # Clean up the temp file on any failure (including KeyboardInterrupt).
        # If os.replace already succeeded the temp path is gone; ignore.
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
