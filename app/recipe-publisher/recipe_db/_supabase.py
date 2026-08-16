"""Singleton Supabase client for `recipe_db` only. Do not re-instantiate.

Was `lib/supabase_client.py`. Supabase is gone from the engine: every table it
once held moved to local Postgres, and `recipe_db` is the sole remaining
caller. It lives here rather than in `lib/` so the engine carries no Supabase
dependency and this dormant, brand-specific tree stays self-contained.

`recipe_db` cannot currently reach a backend at all: `SUPABASE_URL` is set
nowhere -- not `$BRAND_DIR/.env`, not `app/.env`, not `settings.local.json`,
not the containers -- so `get_client()` raises `KeyError`. The recipe flows
that would exercise it are disabled. The live recipe data is the brand's
SQLite file (`$BRAND_DIR/data/db/recipes.db`), which nothing here reads.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_client = None


def _load_secrets() -> None:
    """Load Supabase vars from settings.local.json when not already in env (dev mode)."""
    if os.environ.get("SUPABASE_URL"):
        return
    settings_path = Path(__file__).resolve().parents[2] / ".claude" / "settings.local.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        for k, v in data.get("env", {}).items():
            if k.startswith("SUPABASE"):
                os.environ.setdefault(k, v)


def get_client():
    """Return the shared Supabase client (lazy-initialised)."""
    global _client
    if _client is None:
        _load_secrets()
        from supabase import create_client  # type: ignore[import-untyped]

        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SECRET_KEY"]
        _client = create_client(url, key)
    return _client


def health_check() -> bool:
    """Return True if we can reach Supabase."""
    try:
        get_client().table("worker_runs").select("worker_label").limit(1).execute()
        return True
    except Exception:
        return False
