"""Postgres shim for the engagements DB.

connect() and migrate() are kept as no-ops so existing callers don't need to
change. The schema lives in ``persona/db/schema.sql`` (applied once, either by
the Postgres container's ``/docker-entrypoint-initdb.d`` bootstrap or by a
test fixture) — the table is always available once that schema has run.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def resolve_engagements_db_path() -> None:
    """No-op: engagements DB now lives in Postgres (``persona/db/schema.sql``)."""
    return None


def connect(path: object | None = None) -> None:
    """No-op: engagements DB now lives in Postgres (``persona/db/schema.sql``)."""
    return None


def migrate(conn: object | None = None) -> None:
    """No-op: schema is managed by ``persona/db/schema.sql``, not per-call."""
    logger.info("engagements DB: using Postgres (see persona/db/schema.sql)")
