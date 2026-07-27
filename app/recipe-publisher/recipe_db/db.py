"""Recipe DB connection stub — schema now lives in Supabase.

Callers that do `conn = connect(); migrate(conn); repo = RecipeRepository(conn)`
continue to work: connect() returns None, RecipeRepository ignores the conn arg,
and migrate() is a no-op.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def resolve_db_path() -> Path:
    """Unused placeholder kept for import compat."""
    return Path(__file__).parent / "schema.sql"


def connect(path: Path | None = None) -> None:  # type: ignore[return]
    """No-op: recipe data now lives in Supabase."""
    logger.debug("recipe DB connect() called (Supabase mode — no-op)")


def migrate(conn: Any = None) -> None:
    """No-op: Supabase schema managed externally via scripts/create_supabase_schema.sql."""
    logger.debug("recipe DB migrate() called (Supabase mode — no-op)")
