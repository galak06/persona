"""Thin Postgres access helpers built on the shared pool in `lib/db_pool.py`.

Replaces `lib.supabase_client.get_client()` for the modules migrating off
Supabase this stage -- `groups_db`, `engagements_db`, `worker_db`,
`schedule_db` (tables defined in `db/schema.sql`). Repository rewrites for
those modules are a separate, parallel task; this module only provides the
connection/query primitive they will consume.

`recipes_db`, `content_ideas`, and `oauth_tokens` stay on
`lib.supabase_client` -- this module does not touch them.

Usage::

    from lib.db import execute, fetch_all, fetch_one, get_connection

    rows = fetch_all("SELECT * FROM fb_groups WHERE status = %s", ("joined",))
    row = fetch_one("SELECT * FROM brands WHERE id = %s", (brand_id,))
    execute("UPDATE fb_groups SET status = %s WHERE group_url = %s", (status, url))

    # Multiple statements in one transaction:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO brands (id, name) VALUES (%s, %s)", (bid, name))
        cur.execute("INSERT INTO fb_groups (...) VALUES (...)", (...))
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from lib.db_pool import get_pool
from lib.observability import get_logger

logger = get_logger(__name__)

# psycopg accepts either positional (Sequence) or named (Mapping) query params.
QueryParams = Sequence[Any] | Mapping[str, Any] | None


@contextmanager
def get_connection() -> Iterator[Connection[Any]]:
    """Yield a pooled connection (dict-row cursors by default -- see `lib.db_pool`).

    Commits automatically on clean exit, rolls back on exception -- the
    connection is always returned to the pool afterward. Use this directly
    when several statements must share one transaction; use `execute()` /
    `fetch_all()` / `fetch_one()` for single-statement calls.
    """
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


def execute(query: str, params: QueryParams = None) -> int:
    """Run a write statement (INSERT/UPDATE/DELETE/DDL). Returns the row count."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.rowcount


def fetch_all(query: str, params: QueryParams = None) -> list[dict[str, Any]]:
    """Run a SELECT and return every matching row as a dict."""
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return list(cur.fetchall())


def fetch_one(query: str, params: QueryParams = None) -> dict[str, Any] | None:
    """Run a SELECT and return the first matching row as a dict, or None."""
    with get_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchone()


def health_check() -> bool:
    """Return True if the database is reachable (mirrors supabase_client.health_check)."""
    try:
        fetch_one("SELECT 1")
        return True
    except Exception as exc:
        logger.warning("db_health_check_failed", error=str(exc))
        return False
