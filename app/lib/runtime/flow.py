"""One runner for every cron flow: lock, run-record, health check, exit code.

Each of the 21 scheduled entrypoints under ``scripts/`` used to hand-roll the
same ``if __name__ == "__main__":`` block — acquire the singleton lock, write a
``worker_runs`` row, run, record success or error, translate a held lock into a
clean exit. ``scripts/fb_engager.py`` and ``scripts/ig_engager.py`` differed by
three lines across twenty-one; most other flows implemented a subset, in
different orders, with different answers to the same questions.

That divergence is the point of this module, not the line count. Six flows
never wrote a ``worker_runs`` row at all, so the Schedule page showed them as
never having run. Some treated a held lock as failure and exited non-zero,
turning a healthy overlapping tick into a cron alert. Eleven wrote their own
``_health_check``.

Usage — the whole epilogue becomes one line::

    if __name__ == "__main__":
        raise SystemExit(run_flow("fb-engager", run_fb_engager_scan,
                                  health_check=lambda: session_file_check(SESSION_FILE, "FB")))

Semantics, fixed here so every flow agrees:

* ``--health-check`` short-circuits before the lock and before any run row, so
  probing a flow never looks like a run. Exit code is the probe's.
* A held lock is **success** (exit 0), not failure: a cron tick overlapping a
  still-running scan is normal, and the run row is left alone because
  ``record_start`` happens inside the lock.
* An exception is recorded against the run row and then **re-raised**, so the
  traceback still reaches the cron log and the exit code is non-zero.
* ``main`` may return an exit code, or ``None`` for "success, 0".
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from lib.brand_context import BrandContext
from lib.runtime.singleton import LockAcquisitionError, SingletonLock
from lib.worker_db import record_complete, record_start
from lib.worker_labels import worker_label_for_flow

HEALTH_CHECK_FLAG = "--health-check"


def session_file_check(session_file: Path, label: str) -> int:
    """Probe a Playwright storage-state file. No browser, no network.

    Collapses eleven near-identical ``_health_check`` functions. The ``> 2``
    byte threshold rejects the empty-JSON file a torn-down browser context can
    leave behind, which is why a bare ``exists()`` is not enough.
    """
    if session_file.exists() and session_file.stat().st_size > 2:
        print(f"{label} session OK (storage: {session_file})")
        return 0
    print(f"SESSION_EXPIRED: {session_file} missing or empty", file=sys.stderr)
    return 1


def run_flow(
    flow_id: str,
    main: Callable[[], int | None],
    *,
    health_check: Callable[[], int] | None = None,
    argv: Sequence[str] | None = None,
    worker_label: str | None = None,
) -> int:
    """Run one scheduled flow. Returns the process exit code.

    Args:
        flow_id: The flow's id as it appears in ``profiles/*.json`` and
            ``schedule_tasks`` (e.g. ``"fb-engager"``). Names the singleton
            lock and, via `worker_label_for_flow`, the ``worker_runs`` row.
        main: The work. Returns an exit code, or None for success.
        health_check: Probe for ``--health-check``. Omitted means the flag
            reports healthy without checking anything.
        argv: Defaults to ``sys.argv``.
        worker_label: Override the derived ``worker_runs.worker_label``. Only
            for flows whose row predates `worker_label_for_flow`'s convention.

    Raises:
        Whatever ``main`` raises, after recording the failure.
    """
    args = list(sys.argv if argv is None else argv)
    if HEALTH_CHECK_FLAG in args:
        return health_check() if health_check is not None else 0

    ctx = BrandContext.from_env()
    label = worker_label or worker_label_for_flow(flow_id)

    try:
        with SingletonLock(flow_id):
            # Inside the lock: a tick that never acquired it must not leave a
            # half-written run row behind.
            record_start(ctx.brand_dir, label, ctx.brand_id)
            try:
                code = main()
            except Exception as exc:
                record_complete(ctx.brand_dir, label, ctx.brand_id, "error", str(exc))
                raise
            record_complete(ctx.brand_dir, label, ctx.brand_id, "success")
            return code or 0
    except LockAcquisitionError as exc:
        # Expected under normal cron pacing — the previous tick is still going.
        print(f"another instance of {flow_id!r} is running: {exc}", file=sys.stderr)
        return 0
