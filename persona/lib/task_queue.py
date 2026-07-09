"""Redis-backed task queue for Persona workers.

Producers push task dicts onto a named Redis list.
Consumers call ``pop()`` (blocking) or ``pop_nowait()`` (non-blocking).
Failed tasks are pushed to a dead-letter list for inspection.

Queue names follow the pattern: ``persona:<brand>:<worker>:tasks``
Dead-letter:                     ``persona:<brand>:<worker>:dead``

Usage (producer)::

    from lib.task_queue import TaskQueue
    q = TaskQueue("fb-scan")
    q.push({"group_id": "123", "group_name": "My Dog Group"})

Usage (consumer)::

    q = TaskQueue("fb-scan")
    while True:
        task = q.pop(timeout=10)   # blocks up to 10s
        if task is None:
            break
        try:
            process(task)
            q.ack(task)
        except Exception as exc:
            q.nack(task, str(exc))

Environment variables:
    REDIS_URL        — Redis connection URL (default: redis://localhost:6379/0)
    PERSONA_BRAND    — Brand slug used as Redis key prefix (default: "default")
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import redis

_client: redis.Redis | None = None

DEFAULT_REDIS_URL = "redis://localhost:6379/0"
_NAMESPACE = "persona"
_BRAND = os.environ.get("PERSONA_BRAND", "default")


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        url = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
        _client = redis.from_url(url, decode_responses=True)
    return _client


class TaskQueue:
    def __init__(self, worker: str, brand: str | None = None) -> None:
        self.worker = worker
        _brand = brand or _BRAND
        self._key = f"{_NAMESPACE}:{_brand}:{worker}:tasks"
        self._dead_key = f"{_NAMESPACE}:{_brand}:{worker}:dead"
        self._r = _get_client()

    # ── Producer ─────────────────────────────────────────────────────────────

    def push(self, payload: dict[str, Any]) -> str:
        """Enqueue a task. Returns the generated task_id."""
        task_id = str(uuid.uuid4())
        task = {"task_id": task_id, "enqueued_at": time.time(), **payload}
        self._r.lpush(self._key, json.dumps(task))
        return task_id

    def push_many(self, payloads: list[dict[str, Any]]) -> list[str]:
        pipe = self._r.pipeline()
        ids: list[str] = []
        for p in payloads:
            task_id = str(uuid.uuid4())
            task = {"task_id": task_id, "enqueued_at": time.time(), **p}
            pipe.lpush(self._key, json.dumps(task))
            ids.append(task_id)
        pipe.execute()
        return ids

    # ── Consumer ─────────────────────────────────────────────────────────────

    def pop(self, timeout: int = 30) -> dict[str, Any] | None:
        """Blocking pop. Returns None on timeout."""
        result = self._r.brpop(self._key, timeout=timeout)
        if result is None:
            return None
        _, raw = result
        return json.loads(raw)

    def pop_nowait(self) -> dict[str, Any] | None:
        """Non-blocking pop. Returns None if queue is empty."""
        raw = self._r.rpop(self._key)
        return json.loads(raw) if raw else None

    def ack(self, task: dict[str, Any]) -> None:
        """Acknowledge successful processing (no-op — task already removed on pop)."""

    def nack(self, task: dict[str, Any], reason: str = "") -> None:
        """Move a failed task to the dead-letter list."""
        task["failed_at"] = time.time()
        task["failure_reason"] = reason
        self._r.lpush(self._dead_key, json.dumps(task))

    # ── Inspection ───────────────────────────────────────────────────────────

    def depth(self) -> int:
        return self._r.llen(self._key)

    def dead_count(self) -> int:
        return self._r.llen(self._dead_key)

    def peek(self, n: int = 5) -> list[dict[str, Any]]:
        """Return up to n tasks from the head without removing them."""
        items = self._r.lrange(self._key, -n, -1)
        return [json.loads(i) for i in items]

    def clear(self) -> None:
        self._r.delete(self._key)

    def health_check(self) -> bool:
        try:
            return self._r.ping()
        except Exception:
            return False
