"""Tests for `lib/task_queue.py` (Redis-backed producer/consumer queue).

Real integration tests against a live local Redis, following the
project's `requires_redis` skipif convention (see
`tests/test_api_brand_flows.py`) -- run when one is reachable at
`REDIS_URL` and skip cleanly otherwise; CI's `redis:7-alpine` service
(added in PR7) makes them run for real there. Every test uses its own
dedicated brand/worker namespace and clears it before and after, so
these never touch real queue data.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from lib.task_queue import TaskQueue

_BRAND = "task-queue-test-brand"


def _reachable() -> bool:
    try:
        return TaskQueue(worker="healthcheck", brand=_BRAND).health_check()
    except Exception:
        return False


requires_redis = pytest.mark.skipif(not _reachable(), reason="No reachable Redis at REDIS_URL")


@pytest.fixture
def queue() -> Iterator[TaskQueue]:
    q = TaskQueue(worker="test-worker", brand=_BRAND)
    q.clear()
    q.clear_dead()
    try:
        yield q
    finally:
        q.clear()
        q.clear_dead()


@requires_redis
def test_push_then_pop_nowait_round_trips(queue: TaskQueue) -> None:
    queue.push({"script": "scripts/noop_healthcheck.py"})
    task = queue.pop_nowait()
    assert task is not None
    assert task["script"] == "scripts/noop_healthcheck.py"
    assert "task_id" in task
    assert "enqueued_at" in task


@requires_redis
def test_pop_nowait_returns_none_when_empty(queue: TaskQueue) -> None:
    assert queue.pop_nowait() is None


@requires_redis
def test_pop_blocking_returns_pushed_item(queue: TaskQueue) -> None:
    queue.push({"script": "scripts/noop_healthcheck.py"})
    task = queue.pop(timeout=5)
    assert task is not None
    assert task["script"] == "scripts/noop_healthcheck.py"


@requires_redis
def test_pop_returns_none_on_genuine_timeout_no_exception(queue: TaskQueue) -> None:
    """Regression test: confirmed live (redis-py 7.4.0, PR7's real Docker
    deployment) that an idle blocking BRPOP raised
    `redis.exceptions.TimeoutError` from the socket layer instead of
    returning the documented `None` -- crashing `scripts/task_worker.py
    --loop` every `timeout` seconds (masked into a silent restart loop by
    Docker's `restart: unless-stopped`). `pop()` must never raise for
    "nothing arrived in time," only for a real connection failure.
    """
    start = time.time()
    result = queue.pop(timeout=2)
    elapsed = time.time() - start

    assert result is None
    assert elapsed >= 2  # actually blocked for (approximately) the full window


@requires_redis
def test_push_many_enqueues_all_in_order(queue: TaskQueue) -> None:
    ids = queue.push_many([{"n": 1}, {"n": 2}, {"n": 3}])
    assert len(ids) == 3
    assert len(set(ids)) == 3  # each gets a distinct task_id

    popped = [queue.pop_nowait() for _ in range(3)]
    assert [p["n"] for p in popped if p] == [1, 2, 3]  # FIFO: push order == pop order


@requires_redis
def test_nack_moves_task_to_dead_letter(queue: TaskQueue) -> None:
    queue.push({"script": "scripts/bad.py"})
    task = queue.pop_nowait()
    assert task is not None

    assert queue.dead_count() == 0
    queue.nack(task, "boom")
    assert queue.dead_count() == 1


@requires_redis
def test_depth_and_clear(queue: TaskQueue) -> None:
    queue.push({"n": 1})
    queue.push({"n": 2})
    assert queue.depth() == 2

    queue.clear()
    assert queue.depth() == 0


@requires_redis
def test_peek_does_not_remove_items(queue: TaskQueue) -> None:
    queue.push({"n": 1})
    queue.push({"n": 2})

    preview = queue.peek(5)
    assert len(preview) == 2
    assert queue.depth() == 2  # peek is non-destructive


def test_health_check_false_for_unreachable_redis() -> None:
    q = TaskQueue(worker="healthcheck", brand="unreachable")
    q._r = type("Boom", (), {"ping": lambda self: (_ for _ in ()).throw(ConnectionError())})()  # type: ignore[assignment]
    assert q.health_check() is False
