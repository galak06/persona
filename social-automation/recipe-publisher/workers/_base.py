"""Shared CLI scaffolding for the DB-polling artifact workers.

Each worker provides a ``_targets(repo, seeds, limit)`` selector and a
``_do_one(repo, row)`` task, then calls :func:`run_worker`. This module owns
arg parsing, env + DB bootstrap, the singleton lock, dry-run, and per-row
failure isolation — keeping each ``worker_*.py`` a thin selector + task.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable

from recipe_db import db
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository

from lib.local_env import load_local_env
from lib.runtime.singleton import LockAcquisitionError, SingletonLock

logger = logging.getLogger("workers")

TargetsFn = Callable[[RecipeRepository, list[str], int], list[RecipeRow]]
DoOneFn = Callable[[RecipeRepository, RecipeRow], str]
HealthFn = Callable[[], bool]
PreApplyFn = Callable[[RecipeRepository], None]


def _parse_args(name: str, argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=f"workers.{name}")
    parser.add_argument(
        "--apply", action="store_true", help="do the work (default: dry-run)"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="cap target count (0 = all)"
    )
    parser.add_argument(
        "--seed", action="append", default=[], help="restrict to these recipe ids"
    )
    parser.add_argument(
        "--health-check", action="store_true", help="probe deps, exit 0/1"
    )
    return parser.parse_args(argv)


def _run_apply(
    repo: RecipeRepository,
    targets: list[RecipeRow],
    do_one_fn: DoOneFn,
    name: str,
) -> dict[str, str]:
    """Run the task over each target, isolating per-row failures."""
    outcomes: dict[str, str] = {}
    for row in targets:
        try:
            outcomes[row.id] = do_one_fn(repo, row)
            logger.info("%-16s %-44.44s %s", name, row.id, outcomes[row.id])
        except Exception as exc:
            logger.exception("FAILED %s %s", name, row.id)
            outcomes[row.id] = f"error:{type(exc).__name__}"
    return outcomes


def run_worker(
    name: str,
    *,
    targets_fn: TargetsFn,
    do_one_fn: DoOneFn,
    health_fn: HealthFn | None = None,
    pre_apply_fn: PreApplyFn | None = None,
    argv: list[str] | None = None,
) -> int:
    """Drive one worker: bootstrap, select targets, then dry-run or apply.

    ``pre_apply_fn`` (if given) runs once under the lock BEFORE target selection
    in apply mode — used for write-side pre-passes like audio detection that
    must not fire during a dry-run.
    """
    args = _parse_args(name, argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_local_env()

    if args.health_check:
        ok = health_fn() if health_fn is not None else True
        logger.info("health-check %s: %s", name, "ok" if ok else "FAIL")
        return 0 if ok else 1

    conn = db.connect()
    db.migrate(conn)
    repo = RecipeRepository(conn)

    if not args.apply:
        targets = targets_fn(repo, args.seed, args.limit)
        logger.info("=== DRY-RUN %s (no side effects) ===", name)
        for row in targets:
            logger.info("would %-14s %-44.44s %s", name, row.id, row.name)
        logger.info("targets=%d (run with --apply)", len(targets))
        conn.close()
        return 0

    try:
        with SingletonLock(f"worker_{name}"):
            if pre_apply_fn is not None:
                pre_apply_fn(repo)
            targets = targets_fn(repo, args.seed, args.limit)
            outcomes = _run_apply(repo, targets, do_one_fn, name)
    except LockAcquisitionError as exc:
        logger.warning("another %s instance is running: %s", name, exc)
        conn.close()
        return 0
    conn.close()

    ok = sum(1 for v in outcomes.values() if not v.startswith("error"))
    logger.info("=== %s done: processed=%d ok=%d ===", name, len(outcomes), ok)
    return 0
