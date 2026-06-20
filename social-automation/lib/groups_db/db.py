"""Supabase shim for the Facebook groups DB.

connect() and migrate() are kept as no-ops so existing callers don't need to change.
Schema lives in Supabase; tables are always available.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def resolve_groups_db_path() -> None:
    """No-op: groups DB now lives in Supabase."""
    return None


def connect(path: object | None = None) -> None:
    """No-op: groups DB now lives in Supabase."""
    return None


def migrate(conn: object | None = None) -> None:
    """No-op: schema is managed in Supabase dashboard."""
    logger.info("groups DB: using Supabase (no local migration needed)")
