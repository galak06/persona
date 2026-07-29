"""Suite-wide safety guard: never let destructive tests hit a real database.

Twelve test modules TRUNCATE shared tables on fixture teardown::

    db.execute("TRUNCATE TABLE fb_groups, schedule_tasks, brands CASCADE")

Nothing distinguished the target database, so pointing `DATABASE_URL` at the
live Postgres and running the suite silently destroyed brand registration, the
schedule, the FB groups table and the publish history. That happened on
2026-07-28 while following the project's own documented "run pytest with
DATABASE_URL=..." instruction, and there are no pg_dump backups to restore from.

This module refuses to start such a run. A database is considered disposable
when ANY of:

  * `CI` is set -- GitHub Actions provisions a throwaway postgres service
    container per job, so its `persona` database is safe by construction.
  * the database name ends with `_test` (e.g. `persona_test`) -- the intended
    local workflow.
  * `PERSONA_ALLOW_PROD_DB=1` -- explicit, deliberate override.

Anything else aborts the session before a single test runs.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest

_OVERRIDE_VAR = "PERSONA_ALLOW_PROD_DB"
_TEST_DB_SUFFIX = "_test"


def _database_name(dsn: str) -> str:
    """Database name from a libpq URL, or '' when it has none."""
    return urlparse(dsn).path.lstrip("/")


def _is_disposable(dsn: str) -> bool:
    if os.environ.get("CI"):
        return True
    if os.environ.get(_OVERRIDE_VAR) == "1":
        return True
    return _database_name(dsn).endswith(_TEST_DB_SUFFIX)


def pytest_configure(config: pytest.Config) -> None:
    """Abort the session when DATABASE_URL names a non-disposable database.

    Runs before collection, so no fixture -- and therefore no TRUNCATE -- can
    execute against the wrong database. An unset DATABASE_URL is fine: the DB
    tests skip themselves, which is the documented degraded-but-safe run.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn or _is_disposable(dsn):
        return

    name = _database_name(dsn) or "<none>"
    parsed = urlparse(dsn)
    raise pytest.UsageError(
        f"\nRefusing to run: DATABASE_URL points at database {name!r} on "
        f"{parsed.hostname}:{parsed.port}, which is not disposable.\n"
        f"\nThis suite TRUNCATEs brands, schedule_tasks, fb_groups, engagements, "
        f"worker_runs and completed_tasks. Running it here would destroy live data.\n"
        f"\nCreate a throwaway database instead:\n"
        f"    createdb -h {parsed.hostname} -p {parsed.port} -U {parsed.username} {name}{_TEST_DB_SUFFIX}\n"
        f"    psql <dsn-for-{name}{_TEST_DB_SUFFIX}> -f db/schema.sql\n"
        f"then re-run with DATABASE_URL=...{name}{_TEST_DB_SUFFIX}\n"
        f"\nIf you genuinely mean to target this database, set {_OVERRIDE_VAR}=1.\n"
    )
