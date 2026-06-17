"""PostgreSQL connection pool for DogFoodAndFun automation.

Exposes a single shared psycopg2 ThreadedConnectionPool.  All worker
scripts and the API server use this module so connection objects are
reused across calls rather than opened per-query.

Usage::

    from lib.pg_db import get_conn, release_conn, execute

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    finally:
        release_conn(conn)

    # Or use the context manager helper:
    with db_cursor() as cur:
        cur.execute("INSERT INTO ...")

Environment variables (loaded from settings.local.json via load_local_env):
    PG_DSN  — libpq connection string, e.g.
              ``postgresql://gilcohen@localhost:5432/dogfoodandfun``
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

_pool: pool.ThreadedConnectionPool | None = None

DEFAULT_DSN = "postgresql://gilcohen@localhost:5432/dogfoodandfun"
_MIN_CONN = 1
_MAX_CONN = 10


def _get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        dsn = os.environ.get("PG_DSN", DEFAULT_DSN)
        _pool = pool.ThreadedConnectionPool(_MIN_CONN, _MAX_CONN, dsn)
    return _pool


def get_conn() -> psycopg2.extensions.connection:
    return _get_pool().getconn()


def release_conn(conn: psycopg2.extensions.connection) -> None:
    _get_pool().putconn(conn)


@contextmanager
def db_cursor(
    dict_cursor: bool = False,
    autocommit: bool = False,
) -> Generator[psycopg2.extensions.cursor, None, None]:
    """Context manager: yields a cursor, commits on exit, rolls back on error."""
    conn = get_conn()
    try:
        conn.autocommit = autocommit
        cur_factory = RealDictCursor if dict_cursor else None
        with conn.cursor(cursor_factory=cur_factory) as cur:
            yield cur
        if not autocommit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = False
        release_conn(conn)


def health_check() -> bool:
    """Returns True if the DB is reachable."""
    try:
        with db_cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False
