"""The one Postgres-availability gate for database-backed test modules.

Twenty modules each carried their own copy of this probe, in four textual
variants, all computing the same boolean at import time. The duplication was
harmless in itself; what it cost was visibility. When the probe returns False
every one of those modules skips its whole file, and with twenty independent
copies there was no single place to notice — or report — that a "clean" run
had in fact exercised none of the persistence layer.

`conftest.py` prepares a derived, suite-owned database before collection, so
by the time this runs the only reasons to be unavailable are a server that is
down or a `DATABASE_URL` that was never supplied. Both are reported in the
pytest header.

Usage::

    from tests._pg import requires_postgres

    @requires_postgres
    def test_something(pg): ...
"""

from __future__ import annotations

import pytest

from lib import db


def postgres_available() -> bool:
    """True when the pool can reach the database conftest selected.

    Evaluated once at import — i.e. during collection, which is also when the
    shared pool binds its DSN. That ordering is why `conftest.pytest_configure`
    must settle `DATABASE_URL` before collection rather than before each test.
    """
    try:
        return db.health_check()
    except Exception:
        return False


PG_AVAILABLE = postgres_available()

SKIP_REASON = "No reachable Postgres at DATABASE_URL (or lib.db_pool's local default)"

requires_postgres = pytest.mark.skipif(not PG_AVAILABLE, reason=SKIP_REASON)
