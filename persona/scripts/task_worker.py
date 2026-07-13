#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""Flow-run queue consumer — pure WORKER half of the PR7 producer/consumer split.

Pops `flow-run` queue items pushed by `scripts/task_dispatcher.py` (or the
`POST /brands/{id}/flows/{flow_id}/run` "Run Now" API route) for ANY brand,
runs each item's `script` as a subprocess with that task's own brand
environment (`BRAND_DIR`/`PERSONA_BRAND`/credentials from `<brand_dir>/.env`
-- see `run_task()`), and records start/completion into `worker_runs`
(`lib/worker_db.py`). A single shared worker process serves every brand:
which brands to poll comes from Postgres (`lib.brands_db.list_brands()`),
not this process's own fixed identity -- replacing the one-container-per-
brand model. Every item's `brand`/`brand_dir`/`timeout_seconds` already
travel with the queue payload itself
(`scripts/task_dispatcher.py::build_queue_payload`).

One failing item logs the error, Telegram-notifies, and the loop continues
to the next item -- a single bad task never blocks the rest.

Usage:
    python scripts/task_worker.py            # drain what's queued now, then exit
    python scripts/task_worker.py --loop      # poll every brand continuously
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib import brands_db, worker_db
from lib.brands_db.models import BrandStatus
from lib.local_env import load_brand_env
from lib.observability import get_logger
from lib.task_queue import TaskQueue

logger = get_logger(__name__)

# Must match scripts/task_dispatcher.py's QUEUE_WORKER -- both name the same
# Redis list (`persona:<brand>:flow-run:tasks`), one as producer, one as
# consumer. Not imported from there to keep these two CLI entry points
# independently runnable (mirrors this file's own duplication of
# `_notify_telegram_failure`, an established pattern for this pair).
QUEUE_WORKER = "flow-run"
_DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 600
_DEFAULT_IDLE_SLEEP_SECONDS = 5
_DISPATCHABLE_STATUSES = frozenset({BrandStatus.PROVISIONED, BrandStatus.ACTIVE})


def _notify_telegram_failure(task_id: str, error: str) -> None:
    """Mirrors `task_dispatcher.py`'s own `_notify_telegram_failure`."""
    try:
        import notifier

        notifier.send(f"❌ Task worker failed for <b>{task_id}</b>.\n{error}", silent=False)
    except Exception as exc:
        logger.error("telegram_notify_failed", task_id=task_id, error=str(exc))


def run_task(task: dict[str, Any]) -> None:
    """Execute one queued `flow-run` item, recording status via `lib.worker_db`.

    Builds a per-subprocess environment from this task's own `brand`/
    `brand_dir` (not this worker process's own env, which has no fixed
    brand identity) plus `<brand_dir>/.env`'s brand-specific platform
    credentials (`lib.local_env.load_brand_env`) -- deliberately assembled
    fresh per call rather than merged into `os.environ`, so one brand's
    secrets never leak into another task's subprocess or this long-lived
    process's own global environment.

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
    env = {
        **os.environ,
        "BRAND_DIR": str(brand_dir),
        "PERSONA_BRAND": brand,
        **load_brand_env(brand_dir),
    }
    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        # `TimeoutExpired` carries whatever stdout/stderr the subprocess had
        # already written before being killed -- surfacing it is the
        # difference between "timed out" (no visibility into why) and
        # actually being able to see what the script was doing.
        captured = exc.stderr or exc.stdout or ""
        captured = captured.decode() if isinstance(captured, bytes) else captured
        message = f"timed out after {timeout_seconds}s: {captured.strip()[-500:]}"
        worker_db.record_complete(brand_dir, task_id, brand, "error", message)
        raise
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


def _active_brands() -> list[dict[str, Any]]:
    return [b for b in brands_db.list_brands() if b.get("status") in _DISPATCHABLE_STATUSES]


def drain_all_brands() -> int:
    """Non-blocking: process everything currently queued, across every brand."""
    total = 0
    for brand_row in _active_brands():
        queue = TaskQueue(worker=QUEUE_WORKER, brand=str(brand_row["id"]))
        total += drain_once(queue)
    return total


def run_loop_all_brands(*, idle_sleep: int) -> None:
    """Blocking forever: round-robin non-blocking poll across every brand's queue.

    Uses `pop_nowait()` (not `pop(timeout=...)`) per brand -- a blocking pop
    on one brand's queue would starve every other brand while it waited. A
    full sweep across every brand that finds nothing sleeps `idle_sleep`
    seconds before the next pass.
    """
    while True:
        found_any = False
        for brand_row in _active_brands():
            queue = TaskQueue(worker=QUEUE_WORKER, brand=str(brand_row["id"]))
            task = queue.pop_nowait()
            if task is not None:
                found_any = True
                _process_one(task)
        if not found_any:
            time.sleep(idle_sleep)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consume + execute queued flow-run tasks across every brand"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Poll every brand continuously (default: drain what's queued now, then exit)",
    )
    parser.add_argument(
        "--idle-sleep",
        type=int,
        default=_DEFAULT_IDLE_SLEEP_SECONDS,
        help=(
            "Seconds to sleep after a full sweep across every brand finds "
            f"nothing (default: {_DEFAULT_IDLE_SLEEP_SECONDS})"
        ),
    )
    args = parser.parse_args()

    # BRAND_DIR must still be *set* (lib.config's module-level settings
    # singleton requires it to import at all -- see lib/bootstrap.py), but
    # its value is never consulted below: every task's real brand_dir
    # travels in its own queue payload (run_task() builds the subprocess
    # env from that, not this process's own env), and which brands to poll
    # comes from Postgres (_active_brands()), not a fixed identity.
    from lib.bootstrap import init_script

    init_script(__name__)

    if args.loop:
        logger.info("worker_loop_start")
        run_loop_all_brands(idle_sleep=args.idle_sleep)
    else:
        processed = drain_all_brands()
        logger.info("worker_drain_complete", processed=processed)


if __name__ == "__main__":
    main()
