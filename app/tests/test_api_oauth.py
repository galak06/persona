# pyright: reportMissingImports=false
"""Tests for `api/oauth_api.py`'s brand_id threading.

Handler-level unit tests (monkeypatched `TokenStore`/`FacebookOAuth`, no
network/DB), following `test_api_brand_flows.py`'s pattern: call the route
function directly and monkeypatch its collaborators.

Every route used to default to `os.environ["PERSONA_BRAND"]` -- meaning
every onboarded brand's OAuth connect flow silently read/wrote the SAME
token store regardless of which brand the operator was viewing in the UI.
These tests guard the fix: every route now requires an explicit `brand_id`
and `facebook_callback` recovers it from the `state` token it embedded in
the outbound OAuth URL (Facebook's redirect carries no other Persona-owned
parameter).
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from api import oauth_api
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from lib.oauth.facebook import OAuthToken


class _FakeStore:
    """Captures the brand_id it was constructed with."""

    instances: ClassVar[list[_FakeStore]] = []
    saved: ClassVar[list[OAuthToken]] = []

    def __init__(self, brand_id: str) -> None:
        self.brand_id = brand_id
        _FakeStore.instances.append(self)

    def save(self, token: OAuthToken) -> None:
        _FakeStore.saved.append(token)

    def load(
        self, platform: str, token_type: str = "page", token_id: str = ""
    ) -> OAuthToken | None:
        return OAuthToken(access_token="tok", token_type=token_type, platform=platform)

    def delete(self, platform: str, token_type: str = "page", token_id: str = "") -> None:
        pass

    def list_all(self) -> list[dict[str, Any]]:
        return []


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    oauth_api._pending_states.clear()
    _FakeStore.instances.clear()
    _FakeStore.saved.clear()
    # FacebookOAuth() reads these from the environment even when its methods
    # are monkeypatched below -- CI has no real FB app credentials configured
    # (this is a solo-dev project, not something to put in repo secrets).
    monkeypatch.setenv("FB_APP_ID", "test-app-id")
    monkeypatch.setenv("FB_APP_SECRET", "test-app-secret")


def test_start_oauth_requires_brand_id_over_http() -> None:
    # FastAPI's `Query(...)` "required" enforcement only fires through real
    # request validation, not a direct Python call -- exercise it via
    # TestClient the way an actual missing-param request would.
    app = FastAPI()
    app.include_router(oauth_api.router, prefix="/oauth")
    resp = TestClient(app).get("/oauth/facebook", follow_redirects=False)
    assert resp.status_code == 422


def test_start_oauth_embeds_brand_id_in_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        oauth_api.FacebookOAuth,
        "get_authorization_url",
        lambda self, state="", scopes=None: f"https://facebook.example/auth?state={state}",
    )

    oauth_api.start_facebook_oauth(brand_id="acme-dogs", scopes="")

    assert list(oauth_api._pending_states.values()) == ["acme-dogs"]


def test_callback_recovers_correct_brand_from_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth_api, "TokenStore", _FakeStore)
    monkeypatch.setattr(
        oauth_api.FacebookOAuth,
        "exchange_code",
        lambda self, code: OAuthToken(access_token="short"),
    )
    monkeypatch.setattr(
        oauth_api.FacebookOAuth,
        "exchange_for_long_lived",
        lambda self, short_token: OAuthToken(access_token="long"),
    )

    oauth_api._pending_states["state-for-brand-a"] = "brand-a"
    oauth_api._pending_states["state-for-brand-b"] = "brand-b"

    oauth_api.facebook_callback(
        code="abc123", state="state-for-brand-a", error="", error_description=""
    )

    assert len(_FakeStore.instances) == 1
    assert _FakeStore.instances[0].brand_id == "brand-a"
    # The other brand's pending state must be untouched -- no cross-brand leakage.
    assert oauth_api._pending_states == {"state-for-brand-b": "brand-b"}


def test_callback_rejects_unknown_state() -> None:
    with pytest.raises(HTTPException) as exc_info:
        oauth_api.facebook_callback(
            code="abc123", state="never-issued", error="", error_description=""
        )
    assert exc_info.value.status_code == 400


def test_get_page_token_uses_brand_scoped_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth_api, "TokenStore", _FakeStore)
    monkeypatch.setattr(
        oauth_api.FacebookOAuth,
        "get_page_token",
        lambda self, user_token, page_id: OAuthToken(access_token="page-tok", token_type="page"),
    )

    oauth_api.get_page_token("123456", brand_id="acme-dogs")

    assert _FakeStore.instances[0].brand_id == "acme-dogs"


def test_list_tokens_scopes_to_brand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth_api, "TokenStore", _FakeStore)

    resp = oauth_api.list_tokens(brand_id="acme-dogs")

    assert _FakeStore.instances[0].brand_id == "acme-dogs"
    assert resp.body  # JSONResponse — just confirm it built without error


def test_delete_token_scopes_to_brand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth_api, "TokenStore", _FakeStore)

    oauth_api.delete_token("facebook", "page", brand_id="acme-dogs")

    assert _FakeStore.instances[0].brand_id == "acme-dogs"


def test_refresh_scopes_to_brand_and_404s_when_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    class _EmptyStore(_FakeStore):
        def load(
            self, platform: str, token_type: str = "page", token_id: str = ""
        ) -> OAuthToken | None:
            return None

    monkeypatch.setattr(oauth_api, "TokenStore", _EmptyStore)

    with pytest.raises(HTTPException) as exc_info:
        oauth_api.refresh_facebook_token(brand_id="acme-dogs")

    assert exc_info.value.status_code == 404
    assert _EmptyStore.instances[0].brand_id == "acme-dogs"
