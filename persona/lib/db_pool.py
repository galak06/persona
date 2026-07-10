"""Shared Postgres connection pool for `lib/db.py`.

Wraps a single process-wide `psycopg_pool.ConnectionPool` (psycopg v3),
opened lazily on first use and reused across calls. This is the local-Postgres
successor to `lib/supabase_client.py::get_client()` for the tables in
`db/schema.sql` (`brands`, `fb_groups`, `engagements`, `worker_runs`,
`schedule_tasks`) -- other tables (`recipes`, `content_ideas`, `oauth_tokens`)
stay on Supabase this stage and are untouched by this module.

Environment variables:
    DATABASE_URL -- libpq connection string, e.g.
        ``postgresql://persona:persona@localhost:5432/persona``.
        Falls back to `DEFAULT_DSN` (local dev default) when unset.
"""

from __future__ import annotations

import os
import threading

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from lib.observability import get_logger

logger = get_logger(__name__)

DEFAULT_DSN = "postgresql://localhost:5432/persona"
_MIN_SIZE = 1
_MAX_SIZE = 10
_CONNECT_TIMEOUT_SECONDS = 5  # per-connection TCP connect timeout (libpq)
_CHECKOUT_TIMEOUT_SECONDS = 10  # max wait for `pool.connection()` to hand back a connection

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _dsn() -> str:
    """Resolve the connection string, DATABASE_URL first, local default otherwise."""
    return os.environ.get("DATABASE_URL", DEFAULT_DSN)


def get_pool() -> ConnectionPool:
    """Return the shared connection pool, opening it lazily on first call.

    Every connection handed out uses `dict_row` so query results come back
    as plain dicts, matching the dict-shaped CRUD pattern the rest of the
    codebase (groups_db, engagements_db, etc.) already relies on.
    """
    global _pool
    if _pool is None or _pool.closed:
        with _pool_lock:
            if _pool is None or _pool.closed:
                dsn = _dsn()
                logger.info(
                    "db_pool_opening",
                    min_size=_MIN_SIZE,
                    max_size=_MAX_SIZE,
                )
                _pool = ConnectionPool(
                    dsn,
                    min_size=_MIN_SIZE,
                    max_size=_MAX_SIZE,
                    timeout=_CHECKOUT_TIMEOUT_SECONDS,
                    kwargs={
                        "row_factory": dict_row,
                        "connect_timeout": _CONNECT_TIMEOUT_SECONDS,
                    },
                    open=True,
                )
    return _pool


def close_pool() -> None:
    """Close the shared pool, if open. For test teardown / graceful shutdown."""
    global _pool
    if _pool is not None:
        logger.info("db_pool_closing")
        _pool.close()
        _pool = None
