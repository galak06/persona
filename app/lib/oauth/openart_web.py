"""Server-side OpenArt OAuth web flow (no human terminal involved).

The primary way a brand authorizes OpenArt: the Reels page sends the
operator to `GET /api/v1/oauth/openart/start`, which 302s to OpenArt's
consent screen; OpenArt redirects back to our API callback, which exchanges
the code itself (PKCE, public client -- `token_endpoint_auth_method: none`)
and saves the token into the brand-secrets store. The MCP SDK's own
interactive flow (`python -m lib.oauth.openart`) stays as the terminal
fallback; both write the same storage.

Endpoints are discovered from OpenArt's RFC 8414 metadata (confirmed live:
served at `https://mcp.openart.ai/.well-known/oauth-authorization-server`,
issuer `https://openart.ai`), with the protected-resource metadata as a
fallback hop and spec-default paths as a last resort. The registered client
must list our web callback in `redirect_uris`; `ensure_registered_client`
re-registers via Dynamic Client Registration when it doesn't (DCR is open on
OpenArt -- no portal whitelisting step), keeping the CLI redirect URI too so
both flows share one client.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode, urljoin

import httpx
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from lib.oauth.openart import CLI_REDIRECT_URI, OPENART_MCP_URL
from lib.oauth.openart_store import load_client_info, save_client_info, save_token_record
from lib.observability import get_logger

logger = get_logger(__name__)

_AUTH_BASE_URL = "https://mcp.openart.ai"
_WELL_KNOWN = "/.well-known/oauth-authorization-server"
_PROTECTED_RESOURCE = "/.well-known/oauth-protected-resource"
_HTTP_TIMEOUT = httpx.Timeout(20.0)


class OpenArtWebFlowError(RuntimeError):
    """Discovery, registration, or token exchange failed server-side."""


@dataclass(frozen=True)
class AuthServerMetadata:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str


@dataclass(frozen=True)
class PendingAuthorization:
    """Everything the callback leg needs, stashed server-side keyed by state."""

    authorization_url: str
    state: str
    code_verifier: str
    token_endpoint: str
    client_id: str


def _metadata_from(payload: dict[str, object], base: str) -> AuthServerMetadata:
    return AuthServerMetadata(
        authorization_endpoint=str(payload.get("authorization_endpoint") or urljoin(base, "/authorize")),
        token_endpoint=str(payload.get("token_endpoint") or urljoin(base, "/token")),
        registration_endpoint=str(payload.get("registration_endpoint") or urljoin(base, "/register")),
    )


def discover_metadata(http: httpx.Client) -> AuthServerMetadata:
    """RFC 8414 discovery with the MCP spec's fallback chain."""
    bases = [_AUTH_BASE_URL]
    try:
        prm = http.get(f"{_AUTH_BASE_URL}{_PROTECTED_RESOURCE}", timeout=_HTTP_TIMEOUT)
        if prm.status_code == 200:
            servers = prm.json().get("authorization_servers") or []
            bases.extend(str(s).rstrip("/") for s in servers if str(s).rstrip("/") not in bases)
    except httpx.HTTPError as exc:
        logger.warning("openart_prm_discovery_failed", error=str(exc))

    for base in bases:
        try:
            response = http.get(f"{base}{_WELL_KNOWN}", timeout=_HTTP_TIMEOUT)
        except httpx.HTTPError as exc:
            logger.warning("openart_asm_discovery_failed", base=base, error=str(exc))
            continue
        if response.status_code == 200:
            return _metadata_from(response.json(), base)
    # Spec-default endpoints on the last known base -- same fallback the SDK uses.
    return _metadata_from({}, bases[-1])


def ensure_registered_client(
    http: httpx.Client, metadata: AuthServerMetadata, redirect_uri: str, brand_id: str
) -> str:
    """client_id whose registration covers `redirect_uri`, re-registering via
    DCR when the stored client doesn't (keeps the CLI URI too)."""
    stored = load_client_info(brand_id)
    if stored is not None and any(str(u) == redirect_uri for u in stored.redirect_uris or []):
        return stored.client_id

    registration = {
        "client_name": "Persona Reels Crew",
        "redirect_uris": [CLI_REDIRECT_URI, redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "full_access",
    }
    try:
        response = http.post(metadata.registration_endpoint, json=registration, timeout=_HTTP_TIMEOUT)
    except httpx.HTTPError as exc:
        raise OpenArtWebFlowError(f"OpenArt client registration failed: {exc}") from exc
    if response.status_code not in (200, 201):
        raise OpenArtWebFlowError(
            f"OpenArt client registration rejected ({response.status_code}): {response.text[:300]}"
        )
    info = OAuthClientInformationFull.model_validate(response.json())
    save_client_info(info, brand_id)
    logger.info("openart_client_registered", brand_id=brand_id, redirect_uri=redirect_uri)
    return info.client_id


def begin_authorization(http: httpx.Client, redirect_uri: str, brand_id: str) -> PendingAuthorization:
    """Discovery + DCR + PKCE: everything up to sending the browser away."""
    metadata = discover_metadata(http)
    client_id = ensure_registered_client(http, metadata, redirect_uri, brand_id)

    code_verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(32)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": "full_access",
        "resource": OPENART_MCP_URL,
    }
    return PendingAuthorization(
        authorization_url=f"{metadata.authorization_endpoint}?{urlencode(params)}",
        state=state,
        code_verifier=code_verifier,
        token_endpoint=metadata.token_endpoint,
        client_id=client_id,
    )


def complete_authorization(
    http: httpx.Client,
    pending: PendingAuthorization,
    *,
    code: str,
    redirect_uri: str,
    brand_id: str,
) -> None:
    """Exchange the auth code with our saved PKCE verifier and persist the
    token (with expiry stamp) into the brand-secrets store."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": pending.client_id,
        "code_verifier": pending.code_verifier,
        "resource": OPENART_MCP_URL,
    }
    try:
        response = http.post(pending.token_endpoint, data=data, timeout=_HTTP_TIMEOUT)
    except httpx.HTTPError as exc:
        raise OpenArtWebFlowError(f"OpenArt token exchange failed: {exc}") from exc
    if response.status_code != 200:
        raise OpenArtWebFlowError(
            f"OpenArt token exchange rejected ({response.status_code}): {response.text[:300]}"
        )
    try:
        token = OAuthToken.model_validate(response.json())
    except (ValueError, json.JSONDecodeError) as exc:
        raise OpenArtWebFlowError(f"OpenArt token response is not a valid token: {exc}") from exc
    save_token_record(token, brand_id)
    logger.info("openart_web_authorization_complete", brand_id=brand_id)
