"""Token storage for Persona OAuth tokens.

Stores tokens in Supabase (preferred) with a JSON file fallback for local dev.

Supabase table (run migration in scripts/create_supabase_schema.sql):

    CREATE TABLE IF NOT EXISTS oauth_tokens (
        id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
        brand_id     TEXT NOT NULL,
        platform     TEXT NOT NULL,         -- 'facebook' | 'instagram'
        token_type   TEXT NOT NULL,         -- 'bearer' | 'page'
        token_id     TEXT NOT NULL DEFAULT '', -- page_id or ig_account_id
        access_token TEXT NOT NULL,
        expires_at   TIMESTAMPTZ,
        scope        JSONB DEFAULT '[]',
        created_at   TIMESTAMPTZ DEFAULT NOW(),
        updated_at   TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (brand_id, platform, token_type, token_id)
    );

Usage:
    store = TokenStore(brand_id="mybrand")
    store.save(token)
    token = store.load("facebook", "page")
    if token and token.needs_refresh:
        token = refresh_long_lived_token(token)
        store.save(token)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.oauth.facebook import OAuthToken

# ── Constants ─────────────────────────────────────────────────────────────────

_FALLBACK_DIR = Path(os.environ.get("BRAND_DIR", ".")) / "state" / "oauth_tokens"


# ── Token Store ───────────────────────────────────────────────────────────────


class TokenStore:
    """Read/write OAuth tokens. Supabase-first, JSON fallback."""

    def __init__(self, brand_id: str | None = None) -> None:
        self.brand_id = brand_id or os.environ.get("PERSONA_BRAND", "default")
        self._supabase = self._try_init_supabase()

    # ── Public API ────────────────────────────────────────────────────────────

    def save(self, token: OAuthToken) -> None:
        """Upsert a token (insert or replace on brand+platform+type+id)."""
        if self._supabase:
            self._supabase_upsert(token)
        else:
            self._json_write(token)

    def load(
        self,
        platform: str,
        token_type: str = "page",
        token_id: str = "",
    ) -> OAuthToken | None:
        """Load a token. Returns None if not found."""
        if self._supabase:
            return self._supabase_read(platform, token_type, token_id)
        return self._json_read(platform, token_type, token_id)

    def delete(self, platform: str, token_type: str = "page", token_id: str = "") -> None:
        """Remove a stored token."""
        if self._supabase:
            self._supabase_delete(platform, token_type, token_id)
        else:
            self._json_delete(platform, token_type, token_id)

    def list_all(self) -> list[dict[str, Any]]:
        """Return a summary list of all stored tokens (access_token redacted)."""
        if self._supabase:
            return self._supabase_list()
        return self._json_list()

    # ── Supabase backend ──────────────────────────────────────────────────────

    def _try_init_supabase(self) -> Any | None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            return None
        try:
            from supabase import create_client  # type: ignore[import]
            return create_client(url, key)
        except Exception:
            return None

    def _supabase_upsert(self, token: OAuthToken) -> None:
        assert self._supabase
        row = {
            "brand_id": self.brand_id,
            "platform": token.platform,
            "token_type": token.token_type,
            "token_id": token.token_id,
            "access_token": token.access_token,
            "expires_at": token.expires_at.isoformat() if token.expires_at else None,
            "scope": token.scope,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        (
            self._supabase.table("oauth_tokens")
            .upsert(row, on_conflict="brand_id,platform,token_type,token_id")
            .execute()
        )

    def _supabase_read(
        self, platform: str, token_type: str, token_id: str
    ) -> OAuthToken | None:
        assert self._supabase
        result = (
            self._supabase.table("oauth_tokens")
            .select("*")
            .eq("brand_id", self.brand_id)
            .eq("platform", platform)
            .eq("token_type", token_type)
            .eq("token_id", token_id)
            .maybe_single()
            .execute()
        )
        if not result.data:
            return None
        return OAuthToken.from_dict(result.data)

    def _supabase_delete(self, platform: str, token_type: str, token_id: str) -> None:
        assert self._supabase
        (
            self._supabase.table("oauth_tokens")
            .delete()
            .eq("brand_id", self.brand_id)
            .eq("platform", platform)
            .eq("token_type", token_type)
            .eq("token_id", token_id)
            .execute()
        )

    def _supabase_list(self) -> list[dict[str, Any]]:
        assert self._supabase
        result = (
            self._supabase.table("oauth_tokens")
            .select("platform,token_type,token_id,expires_at,updated_at")
            .eq("brand_id", self.brand_id)
            .execute()
        )
        return result.data or []

    # ── JSON file fallback ────────────────────────────────────────────────────

    def _path(self, platform: str, token_type: str, token_id: str) -> Path:
        slug = f"{platform}_{token_type}_{token_id or 'default'}.json".replace("/", "_")
        _FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        return _FALLBACK_DIR / self.brand_id / slug

    def _json_write(self, token: OAuthToken) -> None:
        p = self._path(token.platform, token.token_type, token.token_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(token.to_dict(), indent=2))

    def _json_read(
        self, platform: str, token_type: str, token_id: str
    ) -> OAuthToken | None:
        p = self._path(platform, token_type, token_id)
        if not p.exists():
            return None
        try:
            return OAuthToken.from_dict(json.loads(p.read_text()))
        except Exception:
            return None

    def _json_delete(self, platform: str, token_type: str, token_id: str) -> None:
        p = self._path(platform, token_type, token_id)
        if p.exists():
            p.unlink()

    def _json_list(self) -> list[dict[str, Any]]:
        brand_dir = _FALLBACK_DIR / self.brand_id
        if not brand_dir.exists():
            return []
        result = []
        for f in brand_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                result.append(
                    {
                        "platform": data.get("platform"),
                        "token_type": data.get("token_type"),
                        "token_id": data.get("token_id"),
                        "expires_at": data.get("expires_at"),
                    }
                )
            except Exception:
                continue
        return result
