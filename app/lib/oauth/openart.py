"""OpenArt MCP OAuth 2.0 client (PKCE + Dynamic Client Registration).

OpenArt's video-generation MCP server (`https://mcp.openart.ai/mcp`) is
authorized via the standard MCP Authorization spec (OAuth 2.0 + PKCE +
Dynamic Client Registration), not an app-id/secret pair like
`lib.oauth.facebook`. The `mcp` SDK already implements the full flow --
discovery, PKCE, DCR, token exchange, refresh -- via
`mcp.client.auth.OAuthClientProvider`; this module only supplies:

  - persistent storage for the resulting tokens + registered client info
    (JSON files under `$BRAND_DIR/state/oauth_tokens/<brand>/openart/`,
    same directory tree `lib.oauth.store.TokenStore` uses for other
    platforms -- though OpenArt's token/client-info shape doesn't fit that
    module's Facebook-specific `OAuthToken` dataclass, so it gets its own
    small file-backed storage here rather than being forced into it)
  - a one-time interactive setup entrypoint: run `build_openart_oauth_provider()`
    once (e.g. `python -m lib.oauth.openart`), approve in the browser, and the
    stored tokens are then reused headlessly by every later reels pipeline run
    -- `OAuthClientProvider` refreshes an expiring token transparently.
"""

from __future__ import annotations

import os
import webbrowser
from pathlib import Path

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

OPENART_MCP_URL = "https://mcp.openart.ai/mcp"
_REDIRECT_URI = "http://localhost:8734/callback"


def _storage_dir() -> Path:
    brand_dir = Path(os.environ.get("BRAND_DIR", "."))
    brand_id = os.environ.get("PERSONA_BRAND", "default")
    d = brand_dir / "state" / "oauth_tokens" / brand_id / "openart"
    d.mkdir(parents=True, exist_ok=True)
    return d


class OpenArtTokenStorage:
    """File-backed implementation of `mcp.client.auth`'s `TokenStorage` protocol."""

    def __init__(self) -> None:
        self._tokens_path = _storage_dir() / "tokens.json"
        self._client_info_path = _storage_dir() / "client_info.json"

    async def get_tokens(self) -> OAuthToken | None:
        if not self._tokens_path.exists():
            return None
        try:
            return OAuthToken.model_validate_json(self._tokens_path.read_text())
        except Exception:
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._tokens_path.write_text(tokens.model_dump_json())

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        if not self._client_info_path.exists():
            return None
        try:
            return OAuthClientInformationFull.model_validate_json(
                self._client_info_path.read_text()
            )
        except Exception:
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._client_info_path.write_text(client_info.model_dump_json())


async def _redirect_handler(auth_url: str) -> None:
    """Interactive step, run once: open the consent URL in a browser."""
    print(f"\nOpen this URL to authorize OpenArt:\n{auth_url}\n")
    webbrowser.open(auth_url)


async def _callback_handler() -> tuple[str, str | None]:
    """Interactive step, run once: prompt for the full redirect URL and parse
    `code`/`state` out of it ourselves.

    Deliberately takes the *whole* URL rather than asking for `code` and
    `state` as two separate manually-copied fragments -- live-reproduced:
    a user reasonably pasted `state=<value>` (the literal query-string
    segment, exactly as it appears in the address bar) rather than just
    `<value>`, which the raw-value prompt wording didn't make clear it
    needed. Parsing the full URL removes that ambiguity entirely -- it's
    also just what's actually sitting in the browser's address bar, no
    manual extraction required.
    """
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


def build_openart_oauth_provider() -> OAuthClientProvider:
    """Construct the OAuth provider used for both the one-time interactive setup
    and headless reuse afterward -- the same call handles both, since
    `OAuthClientProvider` checks stored tokens first and only drives the
    interactive flow when nothing usable is stored yet.
    """
    return OAuthClientProvider(
        server_url=OPENART_MCP_URL,
        client_metadata=OAuthClientMetadata(
            client_name="Persona Reels Crew",
            redirect_uris=[AnyUrl(_REDIRECT_URI)],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope="full_access",
        ),
        storage=OpenArtTokenStorage(),
        redirect_handler=_redirect_handler,
        callback_handler=_callback_handler,
    )


if __name__ == "__main__":
    import anyio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def _setup() -> None:
        """One-time interactive setup: connecting at all drives the OAuth
        provider through discovery + DCR + the interactive consent flow if
        no usable token is stored yet -- the same code path
        `openart_client.py` uses for headless reuse afterward.
        """
        provider = build_openart_oauth_provider()
        async with streamablehttp_client(OPENART_MCP_URL, auth=provider) as (
            read,
            write,
            _get_session_id,
        ), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
        print(f"OpenArt authorization complete -- {len(tools.tools)} tool(s) available.")

    anyio.run(_setup)
