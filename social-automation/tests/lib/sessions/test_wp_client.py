"""Tests for lib.sessions.wp_client.

Uses respx to stub the WP REST endpoint so tests don't hit a real server.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from lib.errors import ConfigurationError
from lib.sessions.wp_client import wp_client


@pytest.fixture(autouse=True)
def _wp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WP_URL", "https://example.com")
    monkeypatch.setenv("WP_USER", "test_user")
    monkeypatch.setenv("WP_APP_PASSWORD", "test_pass")


class TestConfiguration:
    @pytest.mark.parametrize(
        "missing",
        [
            ["WP_URL"],
            ["WP_USER"],
            ["WP_APP_PASSWORD"],
            ["WP_URL", "WP_USER"],
            ["WP_URL", "WP_USER", "WP_APP_PASSWORD"],
        ],
    )
    def test_missing_env_var_raises(
        self, monkeypatch: pytest.MonkeyPatch, missing: list[str]
    ) -> None:
        for var in missing:
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(ConfigurationError) as exc:
            wp_client()
        for var in missing:
            assert var in str(exc.value)

    def test_empty_value_treated_as_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WP_URL", "")
        with pytest.raises(ConfigurationError, match="WP_URL"):
            wp_client()


class TestClientShape:
    def test_returns_httpx_client(self) -> None:
        with wp_client() as client:
            assert isinstance(client, httpx.Client)

    def test_strips_trailing_slash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WP_URL", "https://example.com/")
        with wp_client() as client:
            assert str(client.base_url) == "https://example.com"

    def test_default_timeout(self) -> None:
        with wp_client() as client:
            # httpx Timeout object exposes the connect timeout for inspection.
            assert client.timeout.connect == 30.0

    def test_custom_timeout(self) -> None:
        with wp_client(timeout=5.0) as client:
            assert client.timeout.connect == 5.0

    def test_user_agent_set(self) -> None:
        with wp_client() as client:
            assert "dogfoodandfun" in client.headers.get("user-agent", "").lower()


class TestAuth:
    @respx.mock
    def test_basic_auth_sent(self) -> None:
        route = respx.get("https://example.com/wp-json/wp/v2/posts").mock(
            return_value=httpx.Response(200, json=[])
        )
        with wp_client() as client:
            r = client.get("/wp-json/wp/v2/posts")
        assert r.status_code == 200
        assert route.called
        # respx exposes the captured request — verify Authorization header.
        req = route.calls.last.request
        assert req.headers["authorization"].startswith("Basic ")
