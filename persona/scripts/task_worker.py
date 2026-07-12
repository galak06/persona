#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""Flow-run queue consumer — pure WORKER half of the PR7 producer/consumer split.

Pops `flow-run` queue items pushed by `scripts/task_dispatcher.py` (or the
`POST /brands/{id}/flows/{flow_id}/run` "Run Now" API route) for ONE brand,
runs each item's `script` as a subprocess, and records start/completion into
`worker_runs` (`lib/worker_db.py`) -- the execution half of what
`scripts/task_dispatcher.py` did directly before this split. Every item's
`brand_dir`/`timeout_seconds` travel with the queue payload itself
(`scripts/task_dispatcher.py::build_queue_payload`), so this consumer needs
no `schedule_tasks`/`brands` lookups of its own.

One failing item logs the error, Telegram-notifies, and the loop continues
to the next item -- a single bad task never blocks the rest.

Usage:
    python scripts/task_worker.py            # drain what's queued now, then exit
    python scripts/task_worker.py --loop      # block on the queue, run continuously

`BRAND_DIR` (and the rest of the usual brand env) must be set, same as any
other script in this codebase.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib import worker_db
from lib.observability import get_logger
from lib.task_queue import TaskQueue

logger = get_logger(__name__)

# Must match scripts/task_dispatcher.py's QUEUE_WORKER -- both name the same
# Redis list (`persona:<brand>:flow-run:tasks`), one as producer, one as
# consumer. Not imported from there to keep these two CLI entry points
# independently runnable (mirrors this file's own duplication of
# `_notify_telegram_failure`, an established pattern for this pair).
QUEUE_WORKER = "flow-run"
_DEFAULT_POLL_TIMEOUT_SECONDS = 30
_DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 600


def _notify_telegram_failure(task_id: str, error: str) -> None:
    """Mirrors `task_dispatcher.py`'s own `_notify_telegram_failure`."""
    try:
        import notifier

        notifier.send(f"❌ Task worker failed for <b>{task_id}</b>.\n{error}", silent=False)
    except Exception as exc:
        logger.error("telegram_notify_failed", task_id=task_id, error=str(exc))


def run_task(task: dict[str, Any]) -> None:
    """Execute one queued `flow-run` item, recording status via `lib.worker_db`.

    Raises after recording the failure in `worker_runs` on any error
    (non-zero exit, timeout, launch failure) -- callers catch, log,
    Telegram-notify, and continue to the next item.
    """
    task_id = str(task["schedule_task_id"])
    script = str(task["script"])
    args = [str(a) for a in (task.get("args") or [])]
    brand = str(task["brand"])
    brand_dir = Path(task["brand_dir"])
    timeout_seconds = int(task.get("timeout_seconds") or _DEFAULT_SUBPROCESS_TIMEOUT_SECONDS)

    worker_db.record_start(brand_dir, task_id, brand)
    cmd = [sys.executable, str(PROJECT_ROOT / script), *args]
    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except Exception as exc:
        message = f"failed to launch: {exc}"
        worker_db.record_complete(brand_dir, task_id, brand, "error", message)
        raise

    if result.returncode == 0:
        message = (result.stdout or "").strip()[-500:]
        worker_db.record_complete(brand_dir, task_id, brand, "success", message)
        logger.info("task_executed", task_id=task_id, status="success")
        return

    message = f"exit={result.returncode}: {(result.stderr or '').strip()[-500:]}"
    worker_db.record_complete(brand_dir, task_id, brand, "error", message)
    raise RuntimeError(message)


def _process_one(task: dict[str, Any]) -> None:
    task_id = str(task.get("schedule_task_id") or task.get("task_id"))
    try:
        run_task(task)
    except Exception as exc:
        logger.exception("task_execution_failed", task_id=task_id)
        _notify_telegram_failure(task_id, str(exc))


def drain_once(queue: TaskQueue) -> int:
    """Non-blocking: process everything currently queued. Returns the count processed."""
    processed = 0
    while True:
        task = queue.pop_nowait()
        if task is None:
            break
        _process_one(task)
        processed += 1
    return processed


def run_loop(queue: TaskQueue, *, poll_timeout: int) -> None:
    """Blocking forever: pop (blocks up to `poll_timeout`s), process, repeat."""
    while True:
        task = queue.pop(timeout=poll_timeout)
        if task is None:
            continue
        _process_one(task)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consume + execute queued flow-run tasks for one brand"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Block on the queue and run continuously (default: drain what's queued now, then exit)",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=_DEFAULT_POLL_TIMEOUT_SECONDS,
        help=f"Seconds to block per pop() in --loop mode (default: {_DEFAULT_POLL_TIMEOUT_SECONDS})",
    )
    args = parser.parse_args()

    from lib.bootstrap import init_script

    settings, _log = init_script(__name__)
    if settings.paths is None:
        raise RuntimeError("settings.paths is not configured; is BRAND_DIR set correctly?")
    # PERSONA_BRAND wins when set -- see scripts/task_dispatcher.py::main()'s
    # matching comment for why brand_dir.name alone isn't safe in Docker.
    brand = os.environ.get("PERSONA_BRAND") or settings.paths.brand_dir.name
    queue = TaskQueue(worker=QUEUE_WORKER, brand=brand)

    if args.loop:
        logger.info("worker_loop_start", brand=brand)
        run_loop(queue, poll_timeout=args.poll_timeout)
    else:
        processed = drain_once(queue)
        logger.info("worker_drain_complete", brand=brand, processed=processed)


if __name__ == "__main__":
    main()
