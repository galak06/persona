"""Tests for `GET /api/v1/oauth/openart/status` in `api.oauth_openart_api`.

The compose pre-flight endpoint: reports whether OpenArt is configured for
the resolved brand and whether a usable token is stored. Split out of
`test_oauth_openart_web.py` (which covers the start/callback OAuth web
flow) to keep both files under the 300-line rule. Same approach: a real
ASGI stack over a bare app mounting only this router — no DB, no network.
"""
# ruff: noqa: S101

from __future__ import annotations

from pathlib import Path

import pytest
from api import oauth_openart_api
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    """Bare app mounting only this router -- no DB, no rest of the API."""
    app = FastAPI()
    app.include_router(oauth_openart_api.router, prefix="/api/v1/oauth")
    return TestClient(app, follow_redirects=False)


@pytest.fixture()
def resolved_brand(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[str, str]:
    """Pin `resolve_api_brand` to a known `(brand_id, brand_dir)` -- no DB."""
    resolved = ("b1", str(tmp_path))
    monkeypatch.setattr(oauth_openart_api, "resolve_api_brand", lambda: resolved)
    return resolved


def test_status_not_configured_when_openart_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, resolved_brand: tuple[str, str]
) -> None:
    """A brand without integrations.openart.enabled reports not_configured
    without ever consulting the token store."""
    monkeypatch.setattr(oauth_openart_api, "openart_enabled", lambda _d: False)
    monkeypatch.setattr(
        oauth_openart_api,
        "stored_auth_state",
        lambda _b: pytest.fail("stored_auth_state must not be consulted when disabled"),
    )
    response = client.get("/api/v1/oauth/openart/status")
    assert response.status_code == 200
    assert response.json() == {"state": "not_configured"}


def test_status_missing_without_usable_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, resolved_brand: tuple[str, str]
) -> None:
    monkeypatch.setattr(oauth_openart_api, "openart_enabled", lambda _d: True)
    monkeypatch.setattr(oauth_openart_api, "stored_auth_state", lambda _b: "missing")
    response = client.get("/api/v1/oauth/openart/status")
    assert response.status_code == 200
    assert response.json() == {"state": "missing"}


def test_status_ok_with_stored_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, resolved_brand: tuple[str, str]
) -> None:
    """Happy path, plus: the resolved brand_dir feeds `openart_enabled` and
    the resolved brand_id feeds `stored_auth_state`."""
    enabled_dirs: list[Path] = []
    state_brands: list[str] = []

    def _enabled(brand_dir: Path) -> bool:
        enabled_dirs.append(brand_dir)
        return True

    def _state(brand_id: str) -> str:
        state_brands.append(brand_id)
        return "ok"

    monkeypatch.setattr(oauth_openart_api, "openart_enabled", _enabled)
    monkeypatch.setattr(oauth_openart_api, "stored_auth_state", _state)

    response = client.get("/api/v1/oauth/openart/status")

    assert response.status_code == 200
    assert response.json() == {"state": "ok"}
    assert enabled_dirs == [Path(resolved_brand[1])]
    assert state_brands == [resolved_brand[0]]
