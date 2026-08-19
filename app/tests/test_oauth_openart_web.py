"""Tests for the OpenArt OAuth web flow: `lib.oauth.openart_web` (discovery /
DCR / PKCE / exchange against an httpx.MockTransport) and the
`api.oauth_openart_api` routes. No DB, no network.

The route tests drive a real ASGI stack (`TestClient` over a bare app that
mounts only this router) rather than calling the endpoint functions
directly, which `test_reels_compose_api.py` can do because its parameters
are plain. These endpoints declare `Query(default=...)` parameters: called
directly, those defaults are `Query` *objects*, which are truthy, so a
direct call takes the `if error:` branch on every request and tests nothing
real. Going through the app resolves them the way production does.
"""
# ruff: noqa: S101, SLF001

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from api import oauth_openart_api
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.oauth import openart_web

_ASM = {
    "issuer": "https://openart.ai",
    "authorization_endpoint": "https://openart.ai/suite/api/auth/oauth/authorize",
    "token_endpoint": "https://openart.ai/suite/api/auth/oauth/token",
    "registration_endpoint": "https://openart.ai/suite/api/auth/oauth/register",
}
_REDIRECT_URI = "http://localhost:5001/api/v1/oauth/openart/callback"


@pytest.fixture(autouse=True)
def _brand_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    monkeypatch.setenv("PERSONA_BRAND", "b1")
    return tmp_path


def _client(handler) -> httpx.Client:  # noqa: ANN001
    return httpx.Client(transport=httpx.MockTransport(handler))


def _discovery_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/.well-known/oauth-authorization-server":
        return httpx.Response(200, json=_ASM)
    if request.url.path == "/.well-known/oauth-protected-resource":
        return httpx.Response(
            200, json={"resource": openart_web.OPENART_MCP_URL, "authorization_servers": ["https://openart.ai"]}
        )
    return httpx.Response(404)


# ── lib: discovery / registration / exchange ──────────────────────────────────


def test_discover_metadata_reads_rfc8414_document() -> None:
    with _client(_discovery_handler) as http:
        metadata = openart_web.discover_metadata(http)
    assert metadata.authorization_endpoint == _ASM["authorization_endpoint"]
    assert metadata.token_endpoint == _ASM["token_endpoint"]
    assert metadata.registration_endpoint == _ASM["registration_endpoint"]


def test_begin_authorization_registers_client_and_builds_pkce_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registrations: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/register"):
            body = json.loads(request.content)
            registrations.append(body)
            return httpx.Response(201, json={"client_id": "cid-123", **body})
        return _discovery_handler(request)

    saved: list = []
    monkeypatch.setattr(openart_web, "load_client_info", lambda _b: None)
    monkeypatch.setattr(openart_web, "save_client_info", lambda info, _b: saved.append(info))

    with _client(handler) as http:
        pending = openart_web.begin_authorization(http, _REDIRECT_URI, "b1")

    # DCR happened, covering BOTH redirect URIs (web + CLI), and was persisted.
    assert registrations and _REDIRECT_URI in registrations[0]["redirect_uris"]
    assert openart_web.CLI_REDIRECT_URI in registrations[0]["redirect_uris"]
    assert saved and saved[0].client_id == "cid-123"

    query = parse_qs(urlsplit(pending.authorization_url).query)
    assert query["client_id"] == ["cid-123"]
    assert query["redirect_uri"] == [_REDIRECT_URI]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == [pending.state]
    assert pending.code_verifier  # stays server-side, never in the URL
    assert "code_verifier" not in query
    assert pending.token_endpoint == _ASM["token_endpoint"]


def test_complete_authorization_exchanges_code_and_saves_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchanges: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/oauth/token")
        exchanges.append(parse_qs(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "access_token": "new-at",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "new-rt",
            },
        )

    saved: list = []
    monkeypatch.setattr(openart_web, "save_token_record", lambda token, _b: saved.append(token))
    pending = openart_web.PendingAuthorization(
        authorization_url="https://x",
        state="st",
        code_verifier="verifier-1",
        token_endpoint=_ASM["token_endpoint"],
        client_id="cid-123",
    )
    with _client(handler) as http:
        openart_web.complete_authorization(
            http, pending, code="code-1", redirect_uri=_REDIRECT_URI, brand_id="b1"
        )

    assert exchanges[0]["grant_type"] == ["authorization_code"]
    assert exchanges[0]["code_verifier"] == ["verifier-1"]
    assert exchanges[0]["client_id"] == ["cid-123"]
    assert saved and saved[0].access_token == "new-at"


def test_complete_authorization_raises_on_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    monkeypatch.setattr(openart_web, "save_token_record", lambda *_a: pytest.fail("must not save"))
    pending = openart_web.PendingAuthorization("https://x", "st", "v", _ASM["token_endpoint"], "cid")
    with _client(handler) as http, pytest.raises(openart_web.OpenArtWebFlowError, match="rejected"):
        openart_web.complete_authorization(
            http, pending, code="c", redirect_uri=_REDIRECT_URI, brand_id="b1"
        )


# ── API routes ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_pending() -> None:
    oauth_openart_api._pending.clear()


@pytest.fixture()
def client() -> TestClient:
    """Bare app mounting only this router -- no DB, no rest of the API."""
    app = FastAPI()
    app.include_router(oauth_openart_api.router, prefix="/api/v1/oauth")
    return TestClient(app, follow_redirects=False)


def _pending_auth() -> openart_web.PendingAuthorization:
    return openart_web.PendingAuthorization(
        authorization_url="https://openart.ai/authorize?x=1",
        state="state-1",
        code_verifier="verifier-1",
        token_endpoint=_ASM["token_endpoint"],
        client_id="cid-123",
    )


def _stash(return_to: str = "http://localhost:5173/reels") -> None:
    oauth_openart_api._pending["state-1"] = {
        "pending": _pending_auth(),
        "brand_id": "b1",
        "return_to": return_to,
        "created_at": 10**12,
    }


def test_start_503_when_openart_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oauth_openart_api, "openart_enabled", lambda: False)
    response = client.get("/api/v1/oauth/openart/start", params={"brand_id": "b1"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "openart_not_configured"


def test_start_redirects_to_consent_and_stashes_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oauth_openart_api, "openart_enabled", lambda: True)
    monkeypatch.setattr(oauth_openart_api, "begin_authorization", lambda _h, _r, _b: _pending_auth())

    response = client.get(
        "/api/v1/oauth/openart/start",
        params={"brand_id": "b1", "return_to": "http://localhost:5173/reels"},
    )

    assert response.status_code == 307
    assert response.headers["location"] == "https://openart.ai/authorize?x=1"
    entry = oauth_openart_api._pending["state-1"]
    assert entry["brand_id"] == "b1"
    assert entry["return_to"] == "http://localhost:5173/reels"
    # The PKCE verifier stays server-side -- it must never reach the browser.
    assert entry["pending"].code_verifier == "verifier-1"


def test_start_rejects_non_local_return_to(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Open-redirect guard: a foreign return_to falls back to the default."""
    monkeypatch.setattr(oauth_openart_api, "openart_enabled", lambda: True)
    monkeypatch.setattr(oauth_openart_api, "begin_authorization", lambda _h, _r, _b: _pending_auth())

    client.get(
        "/api/v1/oauth/openart/start",
        params={"brand_id": "b1", "return_to": "https://evil.example/phish"},
    )

    assert oauth_openart_api._pending["state-1"]["return_to"] == oauth_openart_api._DEFAULT_RETURN_TO


def test_callback_exchanges_and_redirects_connected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    completed: list[str] = []

    def _complete(_http, pending, *, code, redirect_uri, brand_id) -> None:  # noqa: ANN001
        assert pending.code_verifier == "verifier-1"  # the SAVED verifier is used
        assert brand_id == "b1"
        completed.append(code)

    monkeypatch.setattr(oauth_openart_api, "complete_authorization", _complete)
    _stash()

    response = client.get(
        "/api/v1/oauth/openart/callback", params={"code": "code-1", "state": "state-1"}
    )

    assert completed == ["code-1"]
    assert response.headers["location"] == "http://localhost:5173/reels?openart=connected"
    assert "state-1" not in oauth_openart_api._pending  # single-use


def test_callback_with_unknown_state_redirects_with_error(client: TestClient) -> None:
    """State mismatch (CSRF/expiry): no exchange, error surfaced on the page."""
    response = client.get(
        "/api/v1/oauth/openart/callback", params={"code": "c", "state": "nope"}
    )
    location = response.headers["location"]
    assert location.startswith(oauth_openart_api._DEFAULT_RETURN_TO)
    assert "openart=error" in location


def test_callback_surfaces_provider_error(client: TestClient) -> None:
    _stash()
    response = client.get(
        "/api/v1/oauth/openart/callback",
        params={"state": "state-1", "error": "access_denied"},
    )
    location = response.headers["location"]
    assert location.startswith("http://localhost:5173/reels?openart=error")
    assert "access_denied" in location


def test_callback_reports_failed_exchange(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: object, **_k: object) -> None:
        raise openart_web.OpenArtWebFlowError("token exchange rejected (400)")

    monkeypatch.setattr(oauth_openart_api, "complete_authorization", _boom)
    _stash()

    response = client.get(
        "/api/v1/oauth/openart/callback", params={"code": "code-1", "state": "state-1"}
    )
    assert "openart=error" in response.headers["location"]
    assert "rejected" in response.headers["location"]


# GET /openart/status tests live in test_oauth_openart_status.py (300-line rule).
