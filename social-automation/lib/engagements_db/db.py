"""Supabase shim for the engagements DB.

connect() and migrate() are kept as no-ops so existing callers don't need to change.
Schema lives in Supabase; table is always available.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def resolve_engagements_db_path() -> None:
    """No-op: engagements DB now lives in Supabase."""
    return None


def connect(path: object | None = None) -> None:
    """No-op: engagements DB now lives in Supabase."""
    return None


def migrate(conn: object | None = None) -> None:
    """No-op: schema is managed in Supabase dashboard."""
    logger.info("engagements DB: using Supabase (no local migration needed)")
