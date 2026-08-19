"""Persistent storage for OpenArt MCP OAuth credentials.

Tokens + registered-client info live in the encrypted Postgres
`brand_secrets` table (same mechanism as the R2 backup credentials), scoped
per brand under the namespaced keys ``openart:tokens`` /
``openart:client_info`` -- the ``:`` marks them as app data, which
`lib.brand_secrets.load_into_environ` skips so multi-KB token JSON never
leaks into process environments.

Records are stamped with an absolute ``expires_at``. This matters because
`mcp.client.auth.OAuthClientProvider` (1.28.x) never restores expiry when it
loads stored tokens: `_initialize()` leaves ``token_expiry_time`` unset, so
`is_token_valid()` answers True for a long-dead access token, the proactive
refresh branch is skipped, the request 401s, and the 401 branch goes
straight to the *interactive* authorization-code grant -- the stored refresh
token is never used. `get_tokens()` therefore returns a stale record with
``access_token=""``: that makes `is_token_valid()` False while
`can_refresh_token()` stays True, which is exactly the state that drives the
provider through its silent refresh path instead.

Legacy JSON files under `$BRAND_DIR/state/oauth_tokens/<brand>/openart/`
(the pre-DB storage) are migrated additively on first load -- copied into
`brand_secrets`, never deleted. If the DB is unreachable on save, the JSON
file is written instead so a freshly-granted token is never dropped.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Literal

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from lib import brand_secrets
from lib.brand_context import current_brand_id
from lib.observability import get_logger

logger = get_logger(__name__)

TOKENS_KEY = "openart:tokens"
CLIENT_INFO_KEY = "openart:client_info"

# Refresh this many seconds before the recorded expiry, absorbing clock skew
# and the request's own latency.
_EXPIRY_SKEW_SECONDS = 60


def resolve_brand_id() -> str:
    """Brand scope for the secrets rows.

    Kept as a named alias because callers inside the OAuth stack already import
    it from here; the rule itself lives in `lib.brand_context`. Nothing outside
    OAuth should reach into this module for brand identity — see
    `api/brand_context.py`, which used to.
    """
    return current_brand_id()


def _legacy_dir() -> Path:
    """Pre-DB storage location; still read for migration and used as the
    write fallback when the DB is unreachable."""
    brand_dir = Path(os.environ.get("BRAND_DIR", "."))
    return brand_dir / "state" / "oauth_tokens" / resolve_brand_id() / "openart"


def _get_secret_json(brand_id: str, key: str) -> dict[str, Any] | None:
    try:
        raw = brand_secrets.get_secret(brand_id, key)
    except brand_secrets.SecretsError as exc:
        logger.warning("openart_secret_read_failed", key=key, error=str(exc))
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        logger.warning("openart_secret_not_json", key=key)
        return None
    return payload if isinstance(payload, dict) else None


def _set_secret_json(brand_id: str, key: str, payload: dict[str, Any]) -> bool:
    try:
        return brand_secrets.set_secret(brand_id, key, json.dumps(payload))
    except brand_secrets.SecretsError as exc:
        logger.warning("openart_secret_write_failed", key=key, error=str(exc))
        return False


# ── Tokens ────────────────────────────────────────────────────────────────────


def load_token_record(brand_id: str | None = None) -> tuple[OAuthToken, float | None] | None:
    """Stored (token, expires_at-epoch) or None. DB first; falls back to the
    legacy JSON file and migrates it (additively -- the file stays)."""
    brand = brand_id or resolve_brand_id()
    record = _get_secret_json(brand, TOKENS_KEY)
    if record is not None and isinstance(record.get("token"), dict):
        try:
            token = OAuthToken.model_validate(record["token"])
        except Exception:
            logger.warning("openart_token_record_invalid", brand_id=brand)
            return None
        expires_at = record.get("expires_at")
        return token, float(expires_at) if isinstance(expires_at, (int, float)) else None

    legacy_path = _legacy_dir() / "tokens.json"
    if not legacy_path.exists():
        return None
    try:
        token = OAuthToken.model_validate_json(legacy_path.read_text())
    except Exception:
        logger.warning("openart_legacy_token_invalid", path=str(legacy_path))
        return None
    # Written before expiry stamping existed -- age unknown, treat as stale
    # (expires_at=None) so the provider refreshes before first use.
    if _set_secret_json(brand, TOKENS_KEY, {"expires_at": None, "token": token.model_dump()}):
        logger.info("openart_legacy_token_migrated", brand_id=brand)
    return token, None


def save_token_record(token: OAuthToken, brand_id: str | None = None) -> None:
    """Persist a token with a fresh absolute expiry stamp. DB is authoritative;
    on DB failure the legacy JSON file is written so the grant isn't lost.

    Carries the stored refresh token forward when the incoming one has none.
    RFC 6749 §6 says a client MUST retain its existing refresh token unless the
    server issues a replacement, and `mcp` 1.28's `_handle_refresh_response()`
    does not honour that -- it validates the refresh response into a fresh
    `OAuthToken` and persists it wholesale. OpenArt does return a refresh token
    on refresh today, but the day it stops, saving that response as-is would
    silently drop our only long-lived credential and turn the very next expiry
    into a manual re-authorization -- exactly the hourly-reauth failure this
    module exists to prevent, arriving from a new direction.
    """
    brand = brand_id or resolve_brand_id()
    if not token.refresh_token:
        previous = load_token_record(brand)
        if previous is not None and previous[0].refresh_token:
            token = token.model_copy(update={"refresh_token": previous[0].refresh_token})
            logger.info("openart_refresh_token_carried_forward", brand_id=brand)
    expires_at = time.time() + token.expires_in if token.expires_in else None
    record = {"expires_at": expires_at, "token": token.model_dump()}
    if _set_secret_json(brand, TOKENS_KEY, record):
        return
    legacy_path = _legacy_dir() / "tokens.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(token.model_dump_json())
    logger.warning("openart_token_saved_to_legacy_file", brand_id=brand, path=str(legacy_path))


# ── Client info ───────────────────────────────────────────────────────────────


def load_client_info(brand_id: str | None = None) -> OAuthClientInformationFull | None:
    brand = brand_id or resolve_brand_id()
    record = _get_secret_json(brand, CLIENT_INFO_KEY)
    if record is not None:
        try:
            return OAuthClientInformationFull.model_validate(record)
        except Exception:
            logger.warning("openart_client_info_invalid", brand_id=brand)
            return None
    legacy_path = _legacy_dir() / "client_info.json"
    if not legacy_path.exists():
        return None
    try:
        info = OAuthClientInformationFull.model_validate_json(legacy_path.read_text())
    except Exception:
        logger.warning("openart_legacy_client_info_invalid", path=str(legacy_path))
        return None
    if _set_secret_json(brand, CLIENT_INFO_KEY, json.loads(info.model_dump_json())):
        logger.info("openart_legacy_client_info_migrated", brand_id=brand)
    return info


def save_client_info(info: OAuthClientInformationFull, brand_id: str | None = None) -> None:
    brand = brand_id or resolve_brand_id()
    if _set_secret_json(brand, CLIENT_INFO_KEY, json.loads(info.model_dump_json())):
        return
    legacy_path = _legacy_dir() / "client_info.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(info.model_dump_json())
    logger.warning("openart_client_info_saved_to_legacy_file", brand_id=brand)


# ── Auth state / protocol adapter ─────────────────────────────────────────────


def stored_auth_state(brand_id: str | None = None) -> Literal["ok", "missing"]:
    """'ok' when a stored token is fresh or refreshable, else 'missing'.

    Deliberately does NOT distinguish "valid" from "refreshable". The only
    question its callers ask -- the Reels page deciding whether to offer
    Connect, the compose pre-flight -- is "does a human have to do something
    before this works?", and a stale-but-refreshable token needs no human:
    the silent refresh is wired and proven. Reporting 'missing' for one would
    nag the operator into a re-authorization that changes nothing, which is
    the same class of lie as an always-visible connect link.

    Cheap: no network. It therefore cannot detect a *revoked* refresh token --
    that surfaces mid-run as OpenArtAuthRequiredError, which is the right
    place for it, since only an actual refresh attempt can prove revocation.
    """
    record = load_token_record(brand_id)
    if record is None:
        return "missing"
    token, expires_at = record
    now = time.time()
    if expires_at is not None and now < expires_at - _EXPIRY_SKEW_SECONDS:
        return "ok"
    return "ok" if token.refresh_token else "missing"


class OpenArtTokenStorage:
    """`mcp.client.auth` TokenStorage protocol over the functions above.
    Methods are async per the protocol but do fast sync DB reads/writes."""

    async def get_tokens(self) -> OAuthToken | None:
        record = load_token_record()
        if record is None:
            return None
        token, expires_at = record
        now = time.time()
        if expires_at is not None and now < expires_at - _EXPIRY_SKEW_SECONDS:
            return token
        if token.refresh_token:
            # Stale (or unknown-age) access token with a refresh token: blank
            # the access token so the provider's is_token_valid() goes False
            # and its silent refresh path runs -- see module docstring.
            return token.model_copy(update={"access_token": ""})
        return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        save_token_record(tokens)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return load_client_info()

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        save_client_info(client_info)
