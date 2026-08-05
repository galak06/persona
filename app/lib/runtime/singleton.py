"""Singleton-runner lock via fcntl.flock — prevents two cron-overlapping invocations.

Generalized from the inline implementation in `scripts/ig_own_comments.py:128-155`,
which only IG-Own-Comments was using. Every runner that mutates shared state
should wrap its main() in `SingletonLock`.

Lock files live at `.claude/state/locks/<name>.lock`. flock() is OS-enforced
and survives crashes — when the process dies, the kernel releases the lock.
That's what we want; no stale-lock cleanup logic needed.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

from lib.errors.base import PermanentError

_DEFAULT_LOCK_DIR = Path(__file__).resolve().parent.parent.parent / ".claude/state/locks"


class LockAcquisitionError(PermanentError):
    """Another instance of the runner is holding the lock.

    Permanent (not retryable) within the current run — the right
    response is to exit cleanly. The next cron tick will retry.
    """


class SingletonLock:
    """Context manager wrapping fcntl.flock — non-blocking exclusive lock.

    On `__enter__`: opens the lock file (creating if needed), attempts
    LOCK_EX | LOCK_NB. If another process holds it, raises
    `LockAcquisitionError`. On `__exit__`: closes the file, which
    drops the lock automatically.

    The lock file's content is the holder's PID, written for
    diagnostics ("which process is blocking me?"). The PID isn't read
    by anyone — flock() handles the actual coordination.

    Args:
        name: Logical runner name. Becomes `<lock_dir>/<name>.lock`.
        lock_dir: Override directory (mostly for tests). Defaults to
            `.claude/state/locks/` under the project root.
    """

    def __init__(
        self,
        name: str,
        *,
        lock_dir: Path | None = None,
    ) -> None:
        if not name or "/" in name:
            raise ValueError(f"singleton lock name must be non-empty and slash-free, got {name!r}")
        self._name: str = name
        self._lock_dir: Path = lock_dir or _DEFAULT_LOCK_DIR
        self._lock_path: Path = self._lock_dir / f"{name}.lock"
        self._handle: IO[str] | None = None

    def __enter__(self) -> SingletonLock:
        self._lock_dir.mkdir(parents=True, exist_ok=True)
        # Open in append-mode-create so we never truncate a live PID
        # if a same-named process exists; flock will reject anyway.
        handle: IO[str] = open(self._lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            other_pid = self._read_holder_pid()
            raise LockAcquisitionError(
                f"another instance of {self._name!r} is running",
                context={"name": self._name, "holder_pid": other_pid},
            ) from exc
        # Write our PID for diagnostics — overwrite any stale value.
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return self

    def __exit__(self, *_args: object) -> None:
        if self._handle is not None:
            # Closing the fd drops the flock; no explicit unlock call needed.
            self._handle.close()
            self._handle = None

    def _read_holder_pid(self) -> int | None:
        """Best-effort read of the holder's PID for error diagnostics."""
        try:
            text = self._lock_path.read_text(encoding="utf-8").strip()
            return int(text) if text else None
        except (OSError, ValueError):
            return None


@contextmanager
def acquire(name: str, *, lock_dir: Path | None = None) -> Iterator[SingletonLock]:
    """Functional alias for `with SingletonLock(name): ...`."""
    with SingletonLock(name, lock_dir=lock_dir) as lock:
        yield lock
