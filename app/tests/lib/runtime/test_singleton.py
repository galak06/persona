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
from lib.runtime.singleton import _BRANDLESS_LOCK_DIR, SingletonLock, acquire, default_lock_dir


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


class TestDefaultLockDir:
    """Where a lock lands when the caller does not name a directory.

    The old default was `<repo>/.claude/state/locks` — inside the worker
    container that is `/app/.claude/state/locks`, covered by no bind mount and
    therefore private to one container's writable layer. Two workers would each
    hold their own copy of "the" lock and run the same flow twice against the
    one shared `/app/brands/<brand>/state`.
    """

    def test_default_is_the_brands_own_state_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BRAND_DIR", str(tmp_path))
        assert default_lock_dir() == tmp_path.resolve() / "state" / "locks"

    def test_a_lock_taken_with_no_lock_dir_lands_under_the_brand(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: the file is really created on the brand's shared mount."""
        monkeypatch.setenv("BRAND_DIR", str(tmp_path))
        with SingletonLock("ig-engager"):
            assert (tmp_path / "state" / "locks" / "ig-engager.lock").exists()

    def test_two_brands_do_not_serialise_against_each_other(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Brand scoping, which the lock NAME alone never provided.

        `SingletonLock("ig-engager")` is the same name for every brand, so one
        shared directory made brand B's ig-engager wait on brand A's for no
        reason — they mutate disjoint state trees.
        """
        brand_a, brand_b = tmp_path / "a", tmp_path / "b"

        monkeypatch.setenv("BRAND_DIR", str(brand_a))
        held = SingletonLock("ig-engager")
        held.__enter__()
        try:
            monkeypatch.setenv("BRAND_DIR", str(brand_b))
            with SingletonLock("ig-engager"):  # must NOT raise
                assert (brand_b / "state" / "locks" / "ig-engager.lock").exists()
            assert (brand_a / "state" / "locks" / "ig-engager.lock").exists()
        finally:
            held.__exit__(None, None, None)

    def test_same_brand_still_contends(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Brand scoping must not weaken the guarantee it exists to provide."""
        monkeypatch.setenv("BRAND_DIR", str(tmp_path))
        held = SingletonLock("ig-engager")
        held.__enter__()
        try:
            with pytest.raises(LockAcquisitionError), SingletonLock("ig-engager"):
                pass
        finally:
            held.__exit__(None, None, None)

    def test_no_brand_falls_back_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI one-offs and tests may have no brand at all.

        `BrandContext.from_env()` raises there. The fallback is the old
        repo-local directory rather than `default_brand_dir()`'s guess: that
        would write `brands/state/locks`, and `default_brand_dir` scans
        `brands/*` for brand folders — it would then count `state` as a second
        brand. With no brand there is no shared brand state to guard anyway.
        """
        monkeypatch.delenv("BRAND_DIR", raising=False)
        assert default_lock_dir() == _BRANDLESS_LOCK_DIR
