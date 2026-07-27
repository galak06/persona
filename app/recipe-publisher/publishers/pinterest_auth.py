"""Pinterest OAuth token refresh + persistence.

Kept separate from the pinterest.py publisher so the 300-line rule stays
enforceable and so backfill / legacy-fix scripts can share the refresh logic
without importing the full publisher surface.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_API_BASE = "https://api.pinterest.com/v5"
_SETTINGS_PATH = (
    Path(__file__).resolve().parents[2] / ".claude" / "settings.local.json"
)


class PinterestAuthError(RuntimeError):
    pass


def refresh_token(warnings: list[str]) -> str:
    """Exchange PINTEREST_REFRESH_TOKEN for a new access_token.

    On success, updates os.environ and persists both tokens back to
    social-automation/.claude/settings.local.json so the next process
    picks them up. Returns the new access_token.
    """
    refresh = os.environ.get("PINTEREST_REFRESH_TOKEN")
    client_id = os.environ.get("PINTEREST_APP_ID")
    client_secret = os.environ.get("PINTEREST_APP_SECRET")
    if not (refresh and client_id and client_secret):
        raise PinterestAuthError(
            "PINTEREST_REFRESH_TOKEN / PINTEREST_APP_ID / "
            "PINTEREST_APP_SECRET must all be set to refresh"
        )
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            f"{_API_BASE}/oauth/token",
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            content=f"grant_type=refresh_token&refresh_token={refresh}",
        )
        if r.status_code >= 400:
            raise PinterestAuthError(
                f"Pinterest token refresh failed: {r.status_code} {r.text[:300]}"
            )
        body = r.json()
    new_access = body["access_token"]
    new_refresh = body.get("refresh_token")
    os.environ["PINTEREST_ACCESS_TOKEN"] = new_access
    if new_refresh:
        os.environ["PINTEREST_REFRESH_TOKEN"] = new_refresh
    _persist(new_access, new_refresh)
    warnings.append("Pinterest token refreshed; settings.local.json updated")
    return new_access


def _persist(access: str, refresh: str | None) -> None:
    try:
        data = json.loads(_SETTINGS_PATH.read_text())
        data["env"]["PINTEREST_ACCESS_TOKEN"] = access
        if refresh:
            data["env"]["PINTEREST_REFRESH_TOKEN"] = refresh
        _SETTINGS_PATH.write_text(json.dumps(data, indent=2) + "\n")
    except Exception as exc:  # noqa: BLE001 — non-fatal
        logger.warning("failed to persist refreshed Pinterest token: %s", exc)
