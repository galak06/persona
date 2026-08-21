"""OpenArt MCP OAuth 2.0 client (PKCE + Dynamic Client Registration).

OpenArt's video/image-generation MCP server (`https://mcp.openart.ai/mcp`)
is authorized via the standard MCP Authorization spec (OAuth 2.0 + PKCE +
DCR). The `mcp` SDK implements the full flow -- discovery, PKCE, DCR, token
exchange, refresh -- via `mcp.client.auth.OAuthClientProvider`; this module
supplies what's around it:

  - persistent storage (`lib.oauth.openart_store`: encrypted `brand_secrets`
    rows, with additive migration from the legacy JSON files)
  - a per-brand enable gate (`openart_enabled`): OpenArt is optional, keyed
    on `integrations.openart.enabled` in `$BRAND_DIR/config.json`
  - **headless safety**: the SDK's authorization-code grant needs a human in
    a browser. That is only allowed when stdin is a real TTY (or the caller
    passes `interactive=True`, as the `python -m lib.oauth.openart` setup
    entrypoint does). Anywhere else -- the API container, cron -- the
    handlers raise `OpenArtAuthRequiredError` instead of blocking on
    `input()`, which previously died with EOFError in the container.

Authorize either from the Reels page ("Authorize OpenArt", the web flow in
`api.oauth_openart_api`) or one-time on a host terminal::

    PERSONA_BRAND=<brand> BRAND_DIR=<brand-dir> python -m lib.oauth.openart

Stored tokens are then reused headlessly; expiring access tokens refresh
silently via the stored refresh token (see `openart_store` for how the
SDK's lost-expiry quirk is worked around).
"""

from __future__ import annotations

import json
import os
import sys
import webbrowser
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata
from pydantic import AnyUrl

from lib.oauth.openart_store import OpenArtTokenStorage, resolve_brand_id
from lib.observability import get_logger

logger = get_logger(__name__)

OPENART_MCP_URL = "https://mcp.openart.ai/mcp"
CLI_REDIRECT_URI = "http://localhost:8734/callback"
_ASM_URL = "https://mcp.openart.ai/.well-known/oauth-authorization-server"

# The endpoints OpenArt's RFC 8414 document actually serves. Discovery is
# still preferred (it is authoritative and would show a migration), but these
# are the floor: without them a failed fetch leaves `oauth_metadata` unset,
# and the SDK's fallback is `<server_base>/token` -- the 404 that used to
# destroy refreshable tokens. A static document deserves a static fallback.
_FALLBACK_METADATA = OAuthMetadata.model_validate(
    {
        "issuer": "https://openart.ai",
        "authorization_endpoint": "https://openart.ai/suite/api/auth/oauth/authorize",
        "token_endpoint": "https://openart.ai/suite/api/auth/oauth/token",
        "registration_endpoint": "https://openart.ai/suite/api/auth/oauth/register",
        "revocation_endpoint": "https://openart.ai/suite/api/auth/oauth/revoke",
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["full_access"],
    }
)

# Process-level cache: the document is static, and the provider is rebuilt
# once per generated image (5x per reel).
_cached_metadata: OAuthMetadata | None = None


def _authorization_server_metadata() -> OAuthMetadata:
    """OpenArt's RFC 8414 metadata, fetched once per process.

    **Load-bearing for silent token refresh.** `mcp` 1.28's `_refresh_token()`
    picks the token endpoint from `context.oauth_metadata`, falling back to
    `<server_base>/token` when it is unset -- and `_initialize()` only loads
    tokens and client info, never metadata (discovery happens in the 401
    branch, which a refresh is supposed to avoid). So every refresh POSTed to
    `https://mcp.openart.ai/token`, got **404**, cleared the tokens and
    dropped into the interactive grant -- confirmed live as
    `WARNING mcp.client.auth.oauth2: Token refresh failed: 404`, which is why
    an expired-but-refreshable token still ended up composing hero-image
    reels. Pre-populating the real endpoint
    (`https://openart.ai/suite/api/auth/oauth/token`) is what makes refresh work.

    Never returns None. Discovery is one GET against a static document, and it
    used to be allowed to fail soft -- but "soft" meant leaving `oauth_metadata`
    unset, which re-armed the 404 above and would cost the operator a manual
    re-authorization over what was only a transient network blip. On any
    failure we fall back to `_FALLBACK_METADATA` instead, so a refreshable
    token is never discarded because a well-known URL was briefly unreachable.
    """
    global _cached_metadata
    if _cached_metadata is not None:
        return _cached_metadata
    try:
        response = httpx.get(_ASM_URL, timeout=15.0)
        if response.status_code == 200:
            _cached_metadata = OAuthMetadata.model_validate(response.json())
        else:
            logger.warning("openart_asm_discovery_failed", status=response.status_code)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("openart_asm_discovery_failed", error=str(exc))
    if _cached_metadata is None:
        _cached_metadata = _FALLBACK_METADATA
    return _cached_metadata


class OpenArtAuthRequiredError(RuntimeError):
    """No usable OpenArt authorization and no human available to grant one.

    Raised instead of prompting whenever the OAuth provider would need the
    interactive consent flow in a non-interactive context (headless API
    container, cron). Carries an actionable message -- surface it verbatim.
    """


def reauth_message(brand_id: str | None = None) -> str:
    brand = brand_id or resolve_brand_id()
    brand_dir = os.environ.get("BRAND_DIR", "<brand-dir>")
    return (
        f"OpenArt authorization required for brand '{brand}'. "
        f"Click 'Authorize OpenArt' on the Reels page, or on the host run: "
        f"PERSONA_BRAND={brand} BRAND_DIR={brand_dir} python -m lib.oauth.openart "
        f"-- then retry Compose."
    )


def openart_enabled(brand_dir: Path | None = None) -> bool:
    """True when the brand opts in via `integrations.openart.enabled` in
    `$BRAND_DIR/config.json`. Absent section/file/flag -> disabled."""
    base = brand_dir or Path(os.environ.get("BRAND_DIR", "."))
    config_path = base / "config.json"
    if not config_path.exists():
        return False
    try:
        config = json.loads(config_path.read_text())
    except ValueError:
        return False
    openart = config.get("integrations", {}).get("openart", {})
    return isinstance(openart, dict) and bool(openart.get("enabled"))


def extract_auth_required(exc: BaseException) -> OpenArtAuthRequiredError | None:
    """First OpenArtAuthRequiredError leaf inside (possibly nested) exception
    groups -- anyio task groups wrap it, sometimes alongside teardown errors."""
    if isinstance(exc, OpenArtAuthRequiredError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            found = extract_auth_required(sub)
            if found is not None:
                return found
    return None


def _make_redirect_handler(interactive: bool) -> Callable[[str], Awaitable[None]]:
    async def _redirect_handler(auth_url: str) -> None:
        if not interactive:
            raise OpenArtAuthRequiredError(reauth_message())
        print(f"\nOpen this URL to authorize OpenArt:\n{auth_url}\n")
        webbrowser.open(auth_url)

    return _redirect_handler


def _make_callback_handler(
    interactive: bool,
) -> Callable[[], Awaitable[tuple[str, str | None]]]:
    async def _callback_handler() -> tuple[str, str | None]:
        """Interactive step: prompt for the full redirect URL and parse
        `code`/`state` out of it (the full URL, not fragments -- live-
        reproduced paste ambiguity with separate code/state prompts)."""
        if not interactive:
            raise OpenArtAuthRequiredError(reauth_message())
        from urllib.parse import parse_qs, urlsplit

        redirect_url = input(
            "Paste the FULL redirect URL from your browser's address bar "
            "(starts with http://localhost:8734/callback...): "
        ).strip()
        query = parse_qs(urlsplit(redirect_url).query)
        code_values = query.get("code")
        state_values = query.get("state")
        code = code_values[0] if code_values else ""
        state = state_values[0] if state_values else None
        if not code:
            raise ValueError(f"no 'code' query param found in pasted URL: {redirect_url!r}")
        return code, state

    return _callback_handler


def build_openart_oauth_provider(interactive: bool | None = None) -> OAuthClientProvider:
    """OAuth provider for both one-time interactive setup and headless reuse.

    `interactive=None` (the default) auto-detects: interactive only when
    stdin is a real TTY. Non-interactive contexts never prompt -- if the
    stored token can't be silently refreshed, the consent handlers raise
    `OpenArtAuthRequiredError` instead.
    """
    resolved = sys.stdin.isatty() if interactive is None else interactive
    provider = OAuthClientProvider(
        server_url=OPENART_MCP_URL,
        client_metadata=OAuthClientMetadata(
            client_name="Persona Reels Crew",
            redirect_uris=[AnyUrl(CLI_REDIRECT_URI)],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope="full_access",
        ),
        storage=OpenArtTokenStorage(),
        redirect_handler=_make_redirect_handler(resolved),
        callback_handler=_make_callback_handler(resolved),
    )
    # Must be set BEFORE the first request: it is what points a silent
    # refresh at the real token endpoint instead of a 404 (see
    # `_authorization_server_metadata`).
    provider.context.oauth_metadata = _authorization_server_metadata()
    return provider


if __name__ == "__main__":
    import anyio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def _setup() -> None:
        """One-time interactive setup: connecting drives the provider through
        discovery + DCR + consent if no usable token is stored yet -- the same
        code path `openart_client.py` then reuses headlessly."""
        provider = build_openart_oauth_provider(interactive=True)
        async with streamablehttp_client(OPENART_MCP_URL, auth=provider) as (
            read,
            write,
            _get_session_id,
        ), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
        print(f"OpenArt authorization complete -- {len(tools.tools)} tool(s) available.")

    anyio.run(_setup)
