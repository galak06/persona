"""Tests for lib.runtime.singleton.

Real fcntl.flock against a tempdir lockfile — no mocking. The OS
contract is what we're verifying."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from lib.runtime import LockAcquisitionError
from lib.runtime.singleton import SingletonLock, acquire


@pytest.fixture
def lock_dir(tmp_path: Path) -> Path:
    return tmp_path / "locks"


class TestBasicAcquireRelease:
    def test_acquire_creates_lock_file(self, lock_dir: Path) -> None:
        with SingletonLock("runner-a", lock_dir=lock_dir):
            assert (lock_dir / "runner-a.lock").exists()

    def test_lock_file_contains_pid(self, lock_dir: Path) -> None:
        with SingletonLock("runner-a", lock_dir=lock_dir):
            content = (lock_dir / "runner-a.lock").read_text().strip()
            assert content == str(os.getpid())

    def test_releases_on_exit(self, lock_dir: Path) -> None:
        with SingletonLock("runner-a", lock_dir=lock_dir):
            pass
        # File still exists — lock release closes the fd, doesn't delete.
        assert (lock_dir / "runner-a.lock").exists()
        # But re-acquiring works.
        with SingletonLock("runner-a", lock_dir=lock_dir):
            pass


class TestContention:
    def test_second_acquire_raises_when_first_held(self, lock_dir: Path) -> None:
        outer = SingletonLock("runner-b", lock_dir=lock_dir)
        outer.__enter__()
        try:
            with (
                pytest.raises(LockAcquisitionError) as exc_info,
                SingletonLock("runner-b", lock_dir=lock_dir),
            ):
                pass
            assert "runner-b" in str(exc_info.value)
            assert exc_info.value.context.get("name") == "runner-b"
        finally:
            outer.__exit__(None, None, None)

    def test_different_names_dont_block(self, lock_dir: Path) -> None:
        with (
            SingletonLock("runner-c", lock_dir=lock_dir),
            SingletonLock("runner-d", lock_dir=lock_dir),
        ):
            pass

    def test_lock_released_after_subprocess_exit(self, lock_dir: Path) -> None:
        """Real OS contract: a child process holding flock loses it on exit,
        even if it crashed without explicit release.
        """
        script = textwrap.dedent(f"""
            import fcntl, os, time
            from pathlib import Path
            os.makedirs({str(lock_dir)!r}, exist_ok=True)
            fh = open({str(lock_dir / "child.lock")!r}, "a+")
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fh.write(str(os.getpid()))
            fh.flush()
            # exit without explicit close — kernel must reclaim
        """)
        subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, timeout=5)
        # Now we should be able to acquire it.
        with SingletonLock("child", lock_dir=lock_dir):
            pass


class TestPidContext:
    def test_holder_pid_in_error_context(self, lock_dir: Path) -> None:
        outer = SingletonLock("runner-e", lock_dir=lock_dir)
        outer.__enter__()
        try:
            with (
                pytest.raises(LockAcquisitionError) as exc_info,
                SingletonLock("runner-e", lock_dir=lock_dir),
            ):
                pass
            assert exc_info.value.context.get("holder_pid") == os.getpid()
        finally:
            outer.__exit__(None, None, None)


class TestNameValidation:
    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError):
            SingletonLock("")

    def test_slash_in_name_rejected(self) -> None:
        with pytest.raises(ValueError):
            SingletonLock("a/b")


class TestFunctionalAlias:
    def test_acquire_works_as_context_manager(self, lock_dir: Path) -> None:
        with acquire("runner-f", lock_dir=lock_dir) as lock:
            assert isinstance(lock, SingletonLock)
            assert (lock_dir / "runner-f.lock").exists()
