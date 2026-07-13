#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""Postgres+Redis task dispatcher — pure PRODUCER (PR7 split).

Reads `schedule_tasks` rows for ONE brand (still one process = one brand,
consistent with the rest of the system this stage), evaluates each row's
`schedule.cron` against `worker_runs`'s last recorded run for that
`(worker_label=task.id, brand)` pair via `lib.scheduling.is_task_due`, takes a
short-lived Redis lock to guard against two dispatcher invocations enqueueing
the SAME row concurrently, then pushes the row onto the brand's Redis
`flow-run` queue (`lib.task_queue.TaskQueue`) for `scripts/task_worker.py`
(the consumer) to actually execute -- this dispatcher never runs a script
itself and never writes to `worker_runs` (the worker does both once it
actually starts the subprocess).

Shape mirrors `scripts/campaign_worker.py`'s croniter due-check loop (that
script is untouched — separate, unrelated dogfoodandfun campaign/recipe
system). The due-check itself is shared via `lib.scheduling.is_task_due`
rather than duplicated.

One failing row (e.g. the Redis push itself fails) logs the error,
Telegram-notifies (mirrors `campaign_worker.py`'s `_notify_telegram_failure`),
and the loop continues to the next row — a single bad task never blocks the
rest.

Usage:
    python scripts/task_dispatcher.py            # single pass, then exit
    python scripts/task_dispatcher.py --loop      # run continuously

`BRAND_DIR` (and the rest of the usual brand env) must be set, same as any
other script in this codebase.
"""

from __future__ import annotations

import argparse
import os
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
from lib.brands_db.models import MANAGED_FLOW_IDS, BrandStatus
from lib.observability import get_logger
from lib.scheduling import is_task_due
from lib.task_queue import TaskQueue

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
QUEUE_WORKER = "flow-run"  # TaskQueue worker name shared with scripts/task_worker.py


class RedisLock(Protocol):
    """Structural type for the one Redis operation this module needs.

    Matches `redis.Redis.set`'s NX/EX contract exactly (`redis-py` returns
    `True` on success, `None` when `nx=True` and the key already exists) so
    tests can substitute a tiny in-memory fake instead of a live server.
    """

    def set(self, name: str, value: str, *, nx: bool = ..., ex: int | None = ...) -> Any: ...


class QueuePusher(Protocol):
    """Structural type for the one `TaskQueue` operation this module needs
    -- tests substitute a tiny in-memory fake instead of a live Redis queue.
    """

    def push(self, payload: dict[str, Any]) -> str: ...


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


def build_queue_payload(
    task: dict[str, Any], *, brand: str, brand_dir: Path, timeout_seconds: int
) -> dict[str, Any]:
    """Shape one `flow-run` queue item from a `schedule_tasks` row.

    `schedule_task_id` (the row's own `id`, e.g. `dogfoodandfun-ig-scanner`)
    is carried explicitly -- distinct from `TaskQueue.push()`'s own
    auto-generated `task_id` (a UUID, just a queue-item identity) -- because
    `scripts/task_worker.py` needs it as `worker_runs.worker_label` to record
    start/completion under the SAME label this dispatcher's own due-check
    reads via `worker_db.get_one()`.
    """
    return {
        "schedule_task_id": str(task.get("id")),
        "script": task["script"],
        "args": [str(a) for a in (task.get("args") or [])],
        "brand": brand,
        "brand_dir": str(brand_dir),
        "timeout_seconds": timeout_seconds,
    }


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
    queue: QueuePusher | None = None,
) -> None:
    """Enqueue one `schedule_tasks` row if it is due and not already locked.

    No-ops (returns without error) when: the row has no `schedule.cron` or
    `script`, it isn't due yet, or a concurrent dispatch already holds its
    lock. Raises if the enqueue itself fails -- `run_once` catches, logs,
    Telegram-notifies, and continues to the next row. Never runs the row's
    script directly -- `scripts/task_worker.py` (the consumer) does, once it
    pops this item off the `flow-run` queue.
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
    payload = build_queue_payload(
        task, brand=brand, brand_dir=brand_dir, timeout_seconds=timeout_seconds
    )
    resolved_queue = queue or TaskQueue(worker=QUEUE_WORKER, brand=brand)
    resolved_queue.push(payload)
    logger.info("task_enqueued", task_id=task_id, script=script)


def run_once(
    *,
    brand: str,
    brand_dir: Path,
    now: datetime | None = None,
    redis_client: RedisLock | None = None,
    queue: QueuePusher | None = None,
) -> None:
    """One dispatch pass: load this brand's due, enabled tasks and enqueue each.

    A single row raising never stops the rest -- logged + Telegram-notified,
    then the pass continues to the next row. A row for a managed flow
    (`ig-scanner`/`fb-scanner`/`fb-group-scout`) not currently in the
    brand's `enabled_flows` is skipped (not treated as an error) -- this is
    what makes disabling a flow in settings take effect on the very next
    dispatch pass, with no re-provisioning or row deletion needed.
    """
    resolved_now = now or datetime.now(UTC)
    resolved_redis = redis_client or _get_redis_client()
    resolved_queue = queue or TaskQueue(worker=QUEUE_WORKER, brand=brand)
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
                queue=resolved_queue,
            )
        except Exception as exc:
            logger.exception("task_dispatch_failed", task_id=task_id)
            _notify_telegram_failure(task_id, str(exc))
            continue


_DISPATCHABLE_STATUSES = frozenset({BrandStatus.PROVISIONED, BrandStatus.ACTIVE})


def run_all_brands(
    *,
    now: datetime | None = None,
    redis_client: RedisLock | None = None,
) -> None:
    """One dispatch pass across every provisioned/active brand.

    A single shared dispatcher replaces the one-container-per-brand model:
    `brand_dir` for each brand comes from `brands.brand_dir` (Postgres,
    already the authoritative per-brand filesystem path — see
    `db/schema.sql`), not a Docker bind-mount convention. `run_once()`
    itself is untouched -- it already takes `brand`/`brand_dir` as plain
    parameters, so looping it over every brand needed no signature change.
    A brand missing its `brand_dir` (not yet provisioned) is skipped, not
    treated as an error.
    """
    resolved_now = now or datetime.now(UTC)
    resolved_redis = redis_client or _get_redis_client()
    for brand_row in brands_db.list_brands():
        if brand_row.get("status") not in _DISPATCHABLE_STATUSES:
            continue
        brand = str(brand_row["id"])
        brand_dir_str = brand_row.get("brand_dir")
        if not brand_dir_str:
            logger.warning("brand_missing_brand_dir", brand=brand)
            continue
        run_once(
            brand=brand,
            brand_dir=Path(brand_dir_str),
            now=resolved_now,
            redis_client=resolved_redis,
        )


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

    # BRAND_DIR must still be *set* (lib.config's module-level settings
    # singleton requires it to import at all -- see lib/bootstrap.py), but
    # its value is never consulted for dispatch decisions below: every
    # brand's own `brand_dir` comes from Postgres (`run_all_brands()`). Any
    # valid brand directory works here; it's satisfying an import
    # requirement, not selecting which brand this process serves.
    from lib.bootstrap import init_script

    init_script(__name__)

    if args.loop:
        logger.info("dispatcher_loop_start", interval=args.interval)
        while True:
            run_all_brands()
            time.sleep(args.interval)
    else:
        run_all_brands()


if __name__ == "__main__":
    main()
