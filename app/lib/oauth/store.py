"""Token storage for Persona OAuth tokens.

Stores tokens as local JSON files, one per (brand, platform, token_type,
token_id), under `$BRAND_DIR/state/oauth_tokens/<brand_id>/`.

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
from pathlib import Path
from typing import Any

from lib.oauth.facebook import OAuthToken

# ── Token Store ───────────────────────────────────────────────────────────────


def _fallback_dir() -> Path:
    """Read `BRAND_DIR` per call, not at import time -- this module is imported
    (e.g. transitively via `api/oauth_api.py`) before `load_brand_env_into_environ()`
    sets `BRAND_DIR` in some processes; a module-level read would freeze onto
    whatever `BRAND_DIR` happened to be at first import, silently misplacing
    every brand's tokens under the wrong directory thereafter.
    """
    return Path(os.environ.get("BRAND_DIR", ".")) / "state" / "oauth_tokens"


class TokenStore:
    """Read/write OAuth tokens as local JSON files."""

    def __init__(self, brand_id: str | None = None) -> None:
        self.brand_id = brand_id or os.environ.get("PERSONA_BRAND", "default")

    # ── Public API ────────────────────────────────────────────────────────────

    def save(self, token: OAuthToken) -> None:
        """Upsert a token (insert or replace on brand+platform+type+id)."""
        self._json_write(token)

    def load(
        self,
        platform: str,
        token_type: str = "page",
        token_id: str = "",
    ) -> OAuthToken | None:
        """Load a token. Returns None if not found."""
        return self._json_read(platform, token_type, token_id)

    def delete(self, platform: str, token_type: str = "page", token_id: str = "") -> None:
        """Remove a stored token."""
        self._json_delete(platform, token_type, token_id)

    def list_all(self) -> list[dict[str, Any]]:
        """Return a summary list of all stored tokens (access_token redacted)."""
        return self._json_list()

    # ── JSON file fallback ────────────────────────────────────────────────────

    def _path(self, platform: str, token_type: str, token_id: str) -> Path:
        slug = f"{platform}_{token_type}_{token_id or 'default'}.json".replace("/", "_")
        fallback_dir = _fallback_dir()
        fallback_dir.mkdir(parents=True, exist_ok=True)
        return fallback_dir / self.brand_id / slug

    def _json_write(self, token: OAuthToken) -> None:
        p = self._path(token.platform, token.token_type, token.token_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(token.to_dict(), indent=2))

    def _json_read(self, platform: str, token_type: str, token_id: str) -> OAuthToken | None:
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
        brand_dir = _fallback_dir() / self.brand_id
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
