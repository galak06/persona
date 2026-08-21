"""Silent-refresh behaviour of the OpenArt OAuth client.

`tests/test_oauth_openart.py` covers the pieces (storage shapes, the
headless guards, metadata pre-population); this file drives the real
`mcp.client.auth.OAuthClientProvider` through its refresh leg and asserts
what the operator actually cares about: an hour-old access token gets
renewed without anyone touching a browser, and the renewal is persisted
with a correct absolute expiry so the *next* process starts fresh too.

Why the SDK needs help at all (mcp 1.28.1, read from the installed source):

  * `_initialize()` loads `current_tokens` and `client_info` and nothing
    else, so `context.token_expiry_time` stays None. `is_token_valid()`
    answers True for any non-empty access token when expiry is unset, so the
    proactive refresh branch never runs for a *stored* token.
    `OpenArtTokenStorage.get_tokens()` answers that by blanking the access
    token of a stale record: is_token_valid() goes False, can_refresh_token()
    stays True, and the provider takes its silent refresh path.
  * `_refresh_token()` reads the token endpoint from `context.oauth_metadata`
    and otherwise POSTs to `<server_base>/token` -- 404 on OpenArt, which
    `_handle_refresh_response()` turns into clear_tokens() plus an
    interactive grant. `build_openart_oauth_provider` pre-populates the
    metadata so that cannot happen.

DB-free: `brand_secrets` is a dict, and no request leaves the process.
"""
# ruff: noqa: S101, SLF001

from __future__ import annotations

import time
from pathlib import Path

import anyio
import httpx
import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from lib import brand_secrets
from lib.oauth import openart, openart_store

TOKEN_ENDPOINT = "https://openart.ai/suite/api/auth/oauth/token"


@pytest.fixture(autouse=True)
def _brand_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    monkeypatch.setenv("PERSONA_BRAND", "b1")


@pytest.fixture(autouse=True)
def _secrets(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(brand_secrets, "get_secret", lambda b, k: store.get((b, k)))
    monkeypatch.setattr(
        brand_secrets, "set_secret", lambda b, k, v: bool(store.__setitem__((b, k), v)) or True
    )
    return store


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery is stubbed to the cached fallback -- no HTTP from any test."""
    monkeypatch.setattr(openart, "_cached_metadata", openart._FALLBACK_METADATA)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)


@pytest.fixture(autouse=True)
def _never_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any `input()` from a headless code path is a hard test failure -- it is
    an EOFError in the container, i.e. a hung/crashed compose run."""

    def _forbidden(*_a: object, **_k: object) -> str:
        raise AssertionError("a non-interactive code path called input()")

    monkeypatch.setattr("builtins.input", _forbidden)


def _store_expired_grant(refresh_token: str = "rt-old") -> None:
    """An access token that died an hour ago, with a live refresh token."""
    openart_store.save_client_info(
        OAuthClientInformationFull.model_validate(
            {"client_id": "cid", "redirect_uris": [openart.CLI_REDIRECT_URI]}
        )
    )
    openart_store.save_token_record(
        OAuthToken.model_validate(
            {
                "access_token": "at-dead",
                "token_type": "Bearer",
                "expires_in": 1,
                "refresh_token": refresh_token,
            }
        )
    )


async def _drive_refresh(response: httpx.Response) -> httpx.Request:
    """Run the provider's auth flow far enough to make it refresh, answer the
    refresh POST with `response`, and hand back the request it issued."""
    provider = openart.build_openart_oauth_provider(interactive=False)
    flow = provider.async_auth_flow(httpx.Request("POST", openart.OPENART_MCP_URL))
    refresh_request = await flow.asend(None)
    try:
        await flow.asend(response)
    except StopAsyncIteration:  # pragma: no cover - flow may end after the retry
        pass
    finally:
        await flow.aclose()
    return refresh_request


def test_expired_token_refreshes_and_persists_absolute_expiry() -> None:
    """The whole point: a stale stored token is renewed silently, against the
    real token endpoint, and written back with an absolute `expires_at` so the
    next process does not start stale all over again."""
    _store_expired_grant()
    before = time.time()

    request = anyio.run(
        _drive_refresh,
        httpx.Response(
            200,
            json={
                "access_token": "at-new",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "rt-new",
            },
        ),
    )

    assert str(request.url) == TOKEN_ENDPOINT, "refresh must not POST to <mcp-host>/token"
    assert b"grant_type=refresh_token" in request.content

    record = openart_store.load_token_record()
    assert record is not None
    token, expires_at = record
    assert token.access_token == "at-new"
    assert token.refresh_token == "rt-new"
    assert expires_at is not None
    assert before + 3600 <= expires_at <= time.time() + 3600


def test_refresh_response_without_refresh_token_keeps_the_stored_one() -> None:
    """RFC 6749 §6: no replacement issued means keep the one we have. The SDK
    persists the refresh response verbatim, so without this the record would
    lose its only long-lived credential and the next expiry would demand a
    manual re-authorization."""
    _store_expired_grant(refresh_token="rt-keep")

    anyio.run(
        _drive_refresh,
        httpx.Response(
            200, json={"access_token": "at-new", "token_type": "Bearer", "expires_in": 3600}
        ),
    )

    record = openart_store.load_token_record()
    assert record is not None
    assert record[0].access_token == "at-new"
    assert record[0].refresh_token == "rt-keep"


def test_rejected_refresh_raises_typed_error_instead_of_prompting() -> None:
    """A revoked refresh token is the one case a human genuinely must fix. It
    has to arrive as `OpenArtAuthRequiredError` with its actionable message --
    never as an `input()` prompt (EOFError in a container) and never as a hang.
    """

    async def _run() -> None:
        _store_expired_grant()
        provider = openart.build_openart_oauth_provider(interactive=False)
        flow = provider.async_auth_flow(httpx.Request("POST", openart.OPENART_MCP_URL))
        await flow.asend(None)
        await flow.asend(httpx.Response(400, json={"error": "invalid_grant"}))
        # Refresh rejected: the SDK drops the tokens and falls through to the
        # interactive grant, which is where we must refuse rather than prompt.
        assert provider.context.current_tokens is None
        await provider._perform_authorization()

    with pytest.raises(openart.OpenArtAuthRequiredError) as excinfo:
        anyio.run(_run)

    assert "Authorize OpenArt" in str(excinfo.value)


def test_stored_auth_state_stays_ok_while_a_refresh_can_still_fix_it() -> None:
    """The deliberate call: 'ok' means "no human needed", not "access token is
    live". A refreshable token needs no human, so the Reels page must not
    offer a Connect button that would change nothing."""
    _store_expired_grant()
    assert openart_store.stored_auth_state() == "ok"


def test_stored_auth_state_missing_once_nothing_can_be_refreshed() -> None:
    openart_store.save_token_record(
        OAuthToken.model_validate({"access_token": "at", "token_type": "Bearer", "expires_in": 1})
    )
    assert openart_store.stored_auth_state() == "missing"
