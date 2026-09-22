"""Concurrency semantics of the two shared JSON state files.

`state/dedup_cache.json` and `state/rate_limit_tracker.json` sit on a bind
mount shared by every process in the stack, and both were written with an
unlocked read-modify-write. `deduplication` truncated-then-wrote the cache and
answered ANY parse failure by overwriting it with `{}` — a transient bad read
became the permanent loss of 60 days of engagement history for both platforms.
`rate_limiter` lost updates: two processes both read `count=3` and both wrote
`4` while five actions had happened, so a daily platform cap could be overshot
without the counter ever showing it.

Both now route their read-modify-write through `lib.io.jsonio.locked_json`
(flock + atomic temp-file replace). These tests pin the resulting guarantees:
the lock is real, the write is a rename, an increment is never lost, and a
cache we cannot parse is preserved rather than destroyed.
"""

from __future__ import annotations

import fcntl
import json
import threading
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from lib import deduplication, rate_limiter
from lib.io.jsonio import lock_path_for

# How long a thread that is expected to BLOCK on the lock is given to prove it
# blocks, and how long one expected to finish is given to finish. Direction,
# not timing, is what is asserted: an unlocked implementation completes
# immediately and fails the first assert regardless of the value.
BLOCKED_PROBE_SECONDS = 0.5
COMPLETION_SECONDS = 10.0


@pytest.fixture
def cache_file(tmp_path: Path) -> Iterator[Path]:
    """Redirect the dedup cache to a temp file. Never touches the real one."""
    path = tmp_path / "dedup_cache.json"
    path.write_text("{}")
    with patch.object(deduplication, "CACHE_FILE", path):
        yield path


@pytest.fixture
def state_file(tmp_path: Path) -> Iterator[Path]:
    """Redirect the rate-limit tracker to a temp file."""
    path = tmp_path / "rate_limit_tracker.json"
    path.write_text("{}")
    with patch.object(rate_limiter, "STATE_FILE", path):
        yield path


def _lock_is_free(path: Path) -> bool:
    """True if nobody holds the exclusive flock guarding `path`.

    The lock lives on `lock_path_for(path)` — a sidecar, because the atomic
    write replaces the data file's inode and a lock held on a replaced inode
    guards nothing. flock is owned by the open file description, so a second
    `open()`, even in this same process, is denied by a lock taken through the
    first — which is what lets this probe run from the test thread.
    """
    with lock_path_for(path).open("a+", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return True


def _assert_blocks_until_unlocked(path: Path, call: Callable[[], object]) -> None:
    """Run `call` while an outside holder owns `path`'s lock; it must wait.

    The deterministic half of the concurrency proof: an unlocked
    read-modify-write sails straight through and fails, every run.
    """
    done = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            call()
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=run, daemon=True)
    with lock_path_for(path).open("a+", encoding="utf-8") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        worker.start()
        blocked = not done.wait(BLOCKED_PROBE_SECONDS)
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)

    worker.join(COMPLETION_SECONDS)
    assert blocked, "the write completed while the file was locked by someone else"
    assert not worker.is_alive(), "the write never completed after the lock was released"
    assert not errors, f"the write raised after acquiring the lock: {errors}"


# ── deduplication: the write is atomic and serialized ─────────────────────


def test_mark_engaged_waits_for_the_cache_lock(cache_file: Path) -> None:
    """A second writer must not modify the cache while the first holds it."""
    _assert_blocks_until_unlocked(
        cache_file,
        lambda: deduplication.mark_engaged("facebook", "p1", "comment"),
    )
    assert deduplication.is_duplicate("facebook", "p1") is True


def test_mark_engaged_replaces_the_cache_instead_of_truncating_it(cache_file: Path) -> None:
    """The write must be a rename, not an in-place truncate.

    An inode change is the observable signature of temp-file + `os.replace`:
    truncate-then-write keeps the inode (and exposes an empty file to any
    concurrent reader in between). No `.tmp` sibling may survive either.
    """
    before = cache_file.stat().st_ino
    deduplication.mark_engaged("instagram", "ig_1", "like", "#dogs")
    after = cache_file.stat().st_ino

    assert after != before, "cache was written in place — a crash mid-write would tear it"
    assert json.loads(cache_file.read_text())["instagram"]["ig_1"]["action"] == "like"
    assert list(cache_file.parent.glob("*.tmp")) == []


def test_concurrent_marks_do_not_lose_each_other(cache_file: Path) -> None:
    """Every interleaved mark survives — no last-writer-wins overwrite."""
    post_ids = [f"post_{i}" for i in range(12)]
    barrier = threading.Barrier(len(post_ids))

    def mark(post_id: str) -> None:
        barrier.wait()
        deduplication.mark_engaged("facebook", post_id, "comment", "Group")

    threads = [threading.Thread(target=mark, args=(pid,)) for pid in post_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(COMPLETION_SECONDS)

    written = json.loads(cache_file.read_text())
    assert sorted(written["facebook"]) == sorted(post_ids)


# ── deduplication: corruption is quarantined, never wiped ─────────────────


def _quarantined(cache_file: Path) -> list[Path]:
    return sorted(cache_file.parent.glob(f"{cache_file.name}.corrupt-*"))


def test_unparseable_cache_is_quarantined_not_destroyed(cache_file: Path) -> None:
    """The old code answered a bad parse with `write_text("{}")`.

    That destroyed 60 days of history for both platforms, silently, with
    nothing left to recover from. The bytes must survive on disk.
    """
    corrupt = '{"facebook": {"p1": {"engaged_at": "2026-09-0'  # torn mid-write
    cache_file.write_text(corrupt)

    assert deduplication.is_duplicate("facebook", "p1") is False

    saved = _quarantined(cache_file)
    assert len(saved) == 1, f"expected exactly one quarantine file, got {saved}"
    assert saved[0].read_text() == corrupt
    assert not cache_file.exists(), "the unreadable file must be moved aside, not left in place"


def test_non_object_cache_is_quarantined(cache_file: Path) -> None:
    """Valid JSON of the wrong shape is just as unusable — same treatment."""
    cache_file.write_text('["not", "an", "object"]')

    assert deduplication.get_cache_stats() == {}

    saved = _quarantined(cache_file)
    assert len(saved) == 1
    assert json.loads(saved[0].read_text()) == ["not", "an", "object"]


def test_a_write_after_quarantine_starts_a_fresh_cache(cache_file: Path) -> None:
    """Recovery path: quarantine, then carry on from empty without crashing."""
    corrupt = "not valid json [[["
    cache_file.write_text(corrupt)

    deduplication.mark_engaged("instagram", "ig_9", "comment")

    assert json.loads(cache_file.read_text())["instagram"]["ig_9"]["action"] == "comment"
    saved = _quarantined(cache_file)
    assert len(saved) == 1
    assert saved[0].read_text() == corrupt


# ── rate_limiter: one lock over the whole read-modify-write ───────────────


def test_record_action_waits_for_the_state_lock(state_file: Path) -> None:
    """The cap check and the increment happen inside one held lock."""
    _assert_blocks_until_unlocked(
        state_file,
        lambda: rate_limiter.record_action("instagram", "like"),
    )
    assert json.loads(state_file.read_text())[date.today().isoformat()]["instagram:like"] == 1


def test_concurrent_record_actions_never_lose_an_increment(state_file: Path) -> None:
    """20 actions must count as 20 — this is the daily cap's whole job.

    The returned counts must be a permutation of 1..20: a lost update shows up
    as two callers handed the same number, with the file below the total.
    """
    workers, per_worker = 4, 5  # instagram:like is capped at 20/day
    assert rate_limiter.DAILY_LIMITS["instagram:like"] == workers * per_worker
    barrier = threading.Barrier(workers)
    returned: list[int] = []
    guard = threading.Lock()

    def record() -> None:
        barrier.wait()
        for _ in range(per_worker):
            count = rate_limiter.record_action("instagram", "like")
            with guard:
                returned.append(count)

    threads = [threading.Thread(target=record) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(COMPLETION_SECONDS)

    assert sorted(returned) == list(range(1, workers * per_worker + 1))
    today = date.today().isoformat()
    assert json.loads(state_file.read_text())[today]["instagram:like"] == workers * per_worker


def test_over_cap_record_leaves_the_state_untouched_and_unlocked(state_file: Path) -> None:
    """A rejected action must not rewrite the file, and must free the lock.

    The cap re-check runs inside the lock, so the RuntimeError comes from
    inside the context manager: the write-back is skipped, the flock released.
    """
    today = date.today().isoformat()
    state_file.write_text(json.dumps({today: {"facebook:comment": 5}}))
    before = state_file.read_text()

    with pytest.raises(RuntimeError, match="Daily limit reached"):
        rate_limiter.record_action("facebook", "comment")

    assert state_file.read_text() == before
    assert _lock_is_free(state_file)


def test_the_lock_is_never_held_across_the_randomized_delay(
    state_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`wait_random_delay` sleeps up to 180s. Holding a flock across that would
    serialize every engager in the stack behind one sleeping process — a worse
    bug than the one being fixed. The lock covers the read-modify-write only.
    """
    free_during_sleep: list[bool] = []

    def fake_sleep(_seconds: float) -> None:
        free_during_sleep.append(_lock_is_free(state_file))

    # String target: resolves through rate_limiter's own `time` import, so
    # this intercepts exactly the sleep `wait_random_delay` performs.
    monkeypatch.setattr("lib.rate_limiter.time.sleep", fake_sleep)

    rate_limiter.record_action("instagram", "like")
    assert _lock_is_free(state_file), "record_action returned still holding the lock"

    rate_limiter.wait_random_delay("instagram", "like")
    assert free_during_sleep == [True]


def test_the_two_locks_never_nest(state_file: Path, cache_file: Path) -> None:
    """No deadlock: each lock is a leaf, taken and released on its own.

    flock is per open-file-description, so a re-entrant acquire from the same
    process would block forever, and two locks taken in opposite orders would
    deadlock. Neither critical section calls back into itself or into the
    other file — this sequence completes, unlocked between every step.
    """
    assert rate_limiter.record_action("wordpress", "reply") == 1
    assert _lock_is_free(state_file) and _lock_is_free(cache_file)

    deduplication.mark_engaged("wordpress", "comment_7", "reply")
    assert _lock_is_free(state_file) and _lock_is_free(cache_file)

    assert rate_limiter.record_action("wordpress", "reply") == 2
    assert deduplication.already_commented("wordpress", "comment_7") is False
    assert _lock_is_free(state_file) and _lock_is_free(cache_file)
