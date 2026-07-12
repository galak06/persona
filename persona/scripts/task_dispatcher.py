#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""Postgres+Redis task dispatcher — Phase A of the scheduling backend.

Reads `schedule_tasks` rows for ONE brand (still one process = one brand,
consistent with the rest of the system this stage), evaluates each row's
`schedule.cron` against `worker_runs`'s last recorded run for that
`(worker_label=task.id, brand)` pair via `lib.scheduling.is_task_due`, takes a
short-lived Redis lock to guard against two dispatcher invocations firing the
same row concurrently, then runs the row's `script` as a subprocess and
records start/completion into `worker_runs` (`lib/worker_db.py`) — this is
the mechanism PR3's brand onboarding relies on to make a brand's
`ig-scanner`/`fb-scanner` actually run on schedule.

Shape mirrors `scripts/campaign_worker.py`'s croniter due-check loop (that
script is untouched — separate, unrelated dogfoodandfun campaign/recipe
system). The due-check itself is shared via `lib.scheduling.is_task_due`
rather than duplicated.

One failing row logs the error, Telegram-notifies (mirrors
`campaign_worker.py`'s `_notify_telegram_failure`), and the loop continues to
the next row — a single bad task never blocks the rest.

Usage:
    python scripts/task_dispatcher.py            # single pass, then exit
    python scripts/task_dispatcher.py --loop      # run continuously

`BRAND_DIR` (and the rest of the usual brand env) must be set, same as any
other script in this codebase.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

import redis

from lib import brands_db, schedule_db, worker_db
from lib.brands_db.models import MANAGED_FLOW_IDS
from lib.observability import get_logger
from lib.scheduling import is_task_due

logger = get_logger(__name__)

_NAMESPACE = "persona"
_DEFAULT_REDIS_URL = "redis://localhost:6379/0"
# Intentionally shorter than a minute: this lock exists to guard against two
# dispatcher invocations racing to dispatch the SAME due row (e.g. a manual
# run overlapping the loop, or two cron-triggered invocations landing close
# together), not to serialize a long-running subprocess -- that's what the
# due-check against worker_runs.last_run already does once it lands. A TTL
# this short still bridges a back-to-back double-invocation while expiring
# well before the next real due minute.
_LOCK_TTL_SECONDS = 45
_DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 600
_DEFAULT_LOOP_INTERVAL_SECONDS = 30


class RedisLock(Protocol):
    """Structural type for the one Redis operation this module needs.

    Matches `redis.Redis.set`'s NX/EX contract exactly (`redis-py` returns
    `True` on success, `None` when `nx=True` and the key already exists) so
    tests can substitute a tiny in-memory fake instead of a live server.
    """

    def set(self, name: str, value: str, *, nx: bool = ..., ex: int | None = ...) -> Any: ...


def _get_redis_client() -> redis.Redis:
    """Open a Redis connection using the same env-var convention as `lib.task_queue`."""
    url = os.environ.get("REDIS_URL", _DEFAULT_REDIS_URL)
    return redis.from_url(url, decode_responses=True)


def _notify_telegram_failure(task_id: str, error: str) -> None:
    """Mirrors `campaign_worker.py`'s `_notify_telegram_failure`."""
    try:
        import notifier

        notifier.send(f"❌ Task dispatcher failed for <b>{task_id}</b>.\n{error}", silent=False)
    except Exception as exc:
        logger.error("telegram_notify_failed", task_id=task_id, error=str(exc))


def _run_subprocess_task(
    task_id: str,
    script: str,
    args: list[str],
    brand_dir: Path,
    brand: str,
    timeout_seconds: int,
) -> None:
    """Execute one due task's script, recording status via `lib.worker_db`.

    Raises after recording the failure in `worker_runs` on any error
    (non-zero exit, timeout, launch failure) -- callers catch, log,
    Telegram-notify, and continue to the next row.
    """
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
        logger.info("task_dispatched", task_id=task_id, status="success")
        return

    message = f"exit={result.returncode}: {(result.stderr or '').strip()[-500:]}"
    worker_db.record_complete(brand_dir, task_id, brand, "error", message)
    raise RuntimeError(message)


def _flow_enabled(task: dict[str, Any], enabled_flows: frozenset[str] | None) -> bool:
    """Whether `task` (a `schedule_tasks` row) is allowed to dispatch.

    Only gates rows whose flow id (`task["title"]`, set by
    `brand_provisioning._flow_to_task`) is one of the 3 onboarding-managed
    flows (`MANAGED_FLOW_IDS`) -- any other row (a legacy WP/recipe
    schedule, say) is unaffected by `enabled_flows` and always allowed.
    `enabled_flows=None` (the brand row couldn't be read) fails open --
    dispatch as before rather than silently stopping every managed flow for
    the brand over a transient lookup problem.
    """
    flow_id = task.get("title")
    if flow_id not in MANAGED_FLOW_IDS:
        return True
    if enabled_flows is None:
        return True
    return flow_id in enabled_flows


def dispatch_task(
    task: dict[str, Any],
    *,
    brand: str,
    brand_dir: Path,
    now: datetime,
    redis_client: RedisLock,
) -> None:
    """Dispatch one `schedule_tasks` row if it is due and not already locked.

    No-ops (returns without error) when: the row has no `schedule.cron` or
    `script`, it isn't due yet, or a concurrent dispatch already holds its
    lock. Raises if the underlying subprocess fails -- `run_once` catches,
    logs, Telegram-notifies, and continues to the next row.
    """
    task_id = str(task.get("id"))
    cron_expr = (task.get("schedule") or {}).get("cron")
    if not cron_expr:
        logger.warning("task_missing_cron", task_id=task_id)
        return

    last_run_row = worker_db.get_one(brand_dir, task_id, brand)
    last_run_iso = last_run_row["last_run"] if last_run_row else None
    if not is_task_due(cron_expr, last_run_iso, now):
        return

    script = task.get("script")
    if not script:
        logger.warning("task_missing_script", task_id=task_id)
        return

    lock_key = f"{_NAMESPACE}:{brand}:dispatch:{task_id}"
    acquired = redis_client.set(lock_key, "1", nx=True, ex=_LOCK_TTL_SECONDS)
    if not acquired:
        logger.info("dispatch_lock_held", task_id=task_id, lock_key=lock_key)
        return

    timeout_minutes = task.get("timeout_minutes")
    timeout_seconds = (
        int(timeout_minutes) * 60 if timeout_minutes else _DEFAULT_SUBPROCESS_TIMEOUT_SECONDS
    )
    args = [str(a) for a in (task.get("args") or [])]
    logger.info("task_dispatch_start", task_id=task_id, script=script)
    _run_subprocess_task(task_id, script, args, brand_dir, brand, timeout_seconds)


def run_once(
    *,
    brand: str,
    brand_dir: Path,
    now: datetime | None = None,
    redis_client: RedisLock | None = None,
) -> None:
    """One dispatch pass: load this brand's due, enabled tasks and dispatch each.

    A single row raising never stops the rest -- logged + Telegram-notified,
    then the pass continues to the next row. A row for a managed flow
    (`ig-scanner`/`fb-scanner`/`fb-group-scout`) not currently in the
    brand's `enabled_flows` is skipped (not treated as an error) -- this is
    what makes disabling a flow in settings take effect on the very next
    dispatch pass, with no re-provisioning or row deletion needed.
    """
    resolved_now = now or datetime.now(UTC)
    resolved_redis = redis_client or _get_redis_client()
    tasks = schedule_db.load_all()
    brand_tasks = [t for t in tasks if t.get("brand_id") == brand]
    logger.info("dispatch_pass_start", brand=brand, task_count=len(brand_tasks))

    brand_row = brands_db.get(brand)
    enabled_flows = frozenset(brand_row["enabled_flows"] or []) if brand_row else None

    for task in brand_tasks:
        task_id = str(task.get("id"))
        if not _flow_enabled(task, enabled_flows):
            logger.info("task_flow_disabled", task_id=task_id, flow_id=task.get("title"))
            continue
        try:
            dispatch_task(
                task,
                brand=brand,
                brand_dir=brand_dir,
                now=resolved_now,
                redis_client=resolved_redis,
            )
        except Exception as exc:
            logger.exception("task_dispatch_failed", task_id=task_id)
            _notify_telegram_failure(task_id, str(exc))
            continue


def main() -> None:
    parser = argparse.ArgumentParser(description="Dispatch due schedule_tasks rows for one brand")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously, sleeping --interval seconds between passes (default: single pass)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=_DEFAULT_LOOP_INTERVAL_SECONDS,
        help=f"Seconds between passes in --loop mode (default: {_DEFAULT_LOOP_INTERVAL_SECONDS})",
    )
    args = parser.parse_args()

    from lib.bootstrap import init_script

    settings, _log = init_script(__name__)
    if settings.paths is None:
        raise RuntimeError("settings.paths is not configured; is BRAND_DIR set correctly?")
    brand_dir = settings.paths.brand_dir
    brand = brand_dir.name

    if args.loop:
        logger.info("dispatcher_loop_start", brand=brand, interval=args.interval)
        while True:
            run_once(brand=brand, brand_dir=brand_dir)
            time.sleep(args.interval)
    else:
        run_once(brand=brand, brand_dir=brand_dir)


if __name__ == "__main__":
    main()
