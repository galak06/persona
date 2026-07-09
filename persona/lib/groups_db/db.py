"""Postgres shim for the Facebook groups DB.

connect() and migrate() are kept as no-ops so existing callers don't need to
change. Schema lives in ``db/schema.sql``, applied once at container/DB init
time (or by test fixtures) — there is nothing left for a per-call migrate()
to do; ``lib/db.py``/``lib/db_pool.py`` own the actual connection.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def resolve_groups_db_path() -> None:
    """No-op: groups DB now lives in Postgres (see ``db/schema.sql``)."""
    return None


def connect(path: object | None = None) -> None:
    """No-op: groups DB now lives in Postgres (see ``db/schema.sql``)."""
    return None


def migrate(conn: object | None = None) -> None:
    """No-op: schema is applied from ``db/schema.sql``, not per-call."""
    logger.info("groups DB: using Postgres (schema applied from db/schema.sql)")
