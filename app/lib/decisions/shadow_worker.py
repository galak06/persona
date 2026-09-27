"""One daemon thread draining a FIFO queue of gate tasks (shadow mode).

A daemon thread, not a ``ThreadPoolExecutor``: the executor's threads are
joined at interpreter exit, so a Jev call still in flight when the flow ends
would hold the process open. A daemon thread is simply abandoned at exit --
after ``drain`` has given it a bounded chance to finish.

FIFO on one thread is the ordering guarantee the gates rely on: a task
submitted after another always runs after it.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from typing import Final

from lib.observability import get_logger

log = get_logger(__name__)

_STOP: Final = object()


class ShadowWorker:
    """Lazily started; ``submit`` never blocks; ``drain`` never raises."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._queue: queue.Queue[object] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._closed = False

    def submit(self, task: Callable[[], object]) -> None:
        """Queue ``task``; ignored once the worker is draining or drained."""
        if self._closed:
            return
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
            self._thread.start()
        self._queue.put(task)

    def drain(self, timeout_s: float) -> int:
        """Stop taking work and wait up to ``timeout_s`` for the queue to
        empty. Returns how many tasks were left undone (0 = fully drained)."""
        try:
            self._closed = True
            if self._thread is None:
                return 0
            self._queue.put(_STOP)
            self._thread.join(timeout=max(0.0, timeout_s))
            if not self._thread.is_alive():
                return 0
            # Out of time: drop what is still queued (like cancel_futures) so
            # the thread exits right after the task in flight.
            dropped = 0
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                dropped += int(item is not _STOP)
            self._queue.put(_STOP)
            return dropped + 1  # the dropped tasks plus the one in flight
        except Exception as exc:
            log.warning("jev_gate_error", stage="drain", error_type=type(exc).__name__)
            return -1

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is _STOP:
                return
            started = time.monotonic()
            try:
                if callable(task):
                    task()
            except Exception as exc:  # one bad task must not kill the worker
                log.warning(
                    "jev_gate_error",
                    stage="worker",
                    error_type=type(exc).__name__,
                    elapsed_s=round(time.monotonic() - started, 2),
                )
