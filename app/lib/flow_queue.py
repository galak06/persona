"""The `flow-run` queue contract: worker name, payload shape, and how to enqueue.

One flow-run item crosses three processes — the API or dispatcher builds it,
Redis carries it, `scripts/task_worker.py` executes it — so its shape is a
contract, not an implementation detail. It was previously written out by hand
in five places and consumed in a sixth:

    api/brand_flows_api.py, api/ideas_generate_api.py,
    api/reels_compose_api.py, api/social_posts_compose_api.py
    scripts/task_dispatcher.py::build_queue_payload   (the real one, uncalled
                                                       by any of the four)
    scripts/task_worker.py                            (the consumer)

Each of the five redeclared `_QUEUE_WORKER = "flow-run"` as a local constant,
two of them carrying a comment asking the reader to keep it in sync with
another file. `task_worker` declined to import the builder on purpose, "to
keep these two CLI entry points independently runnable" — a real constraint
when the shape lived under `scripts/`, and one this module dissolves by
living in `lib/`, which every process already imports.

Getting the shape wrong fails quietly and at a distance: the item is enqueued,
the caller reports "started", and the run dies in another container.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lib.task_queue import TaskQueue

# The TaskQueue worker name every flow-run producer and the consumer share.
# Previously six independent string literals.
FLOW_RUN_WORKER = "flow-run"


def build_payload(
    *,
    schedule_task_id: str,
    script: str,
    brand: str,
    brand_dir: str | Path,
    args: Sequence[str] = (),
    timeout_seconds: int,
    headless: bool | None = None,
) -> dict[str, Any]:
    """Shape one `flow-run` item.

    `schedule_task_id` is the `schedule_tasks` row id (e.g.
    `dogfoodandfun-ig-engager`), deliberately distinct from `TaskQueue.push()`'s
    auto-generated `task_id` (a UUID identifying the queue item). The worker
    records `worker_runs` under this id, which is the same label the
    dispatcher's due-check and the `/workers` endpoint read — a mismatch here
    means a flow that runs but is displayed as never having run.

    `brand_dir` must be the path as the WORKER will see it (the value stored in
    `brands.brand_dir`, i.e. the container mount), never the caller's own
    `BRAND_DIR`. The two differ whenever the API runs outside the worker's
    container, and the failure is a run that dies against a path that does not
    exist there.

    `headless` is omitted entirely when None, so the worker falls back to the
    brand's own runtime setting rather than receiving an explicit default.
    """
    payload: dict[str, Any] = {
        "schedule_task_id": schedule_task_id,
        "script": script,
        "args": [str(a) for a in args],
        "brand": brand,
        "brand_dir": str(brand_dir),
        "timeout_seconds": timeout_seconds,
    }
    if headless is not None:
        payload["headless"] = headless
    return payload


def payload_from_task(
    task: dict[str, Any],
    *,
    brand: str,
    brand_dir: str | Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Shape an item from a `schedule_tasks` row (the dispatcher's path)."""
    return build_payload(
        schedule_task_id=str(task.get("id")),
        script=str(task["script"]),
        brand=brand,
        brand_dir=brand_dir,
        args=[str(a) for a in (task.get("args") or [])],
        timeout_seconds=timeout_seconds,
    )


def dispatch(
    *,
    schedule_task_id: str,
    script: str,
    brand: str,
    brand_dir: str | Path,
    args: Sequence[str] = (),
    timeout_seconds: int,
    headless: bool | None = None,
) -> str:
    """Build and enqueue one flow-run item. Returns the queue's `task_id`.

    The one call an API route needs: producers no longer name the worker, so
    they cannot name it wrongly.
    """
    payload = build_payload(
        schedule_task_id=schedule_task_id,
        script=script,
        brand=brand,
        brand_dir=brand_dir,
        args=args,
        timeout_seconds=timeout_seconds,
        headless=headless,
    )
    return TaskQueue(worker=FLOW_RUN_WORKER, brand=brand).push(payload)
