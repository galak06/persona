"""Tests for `lib.oauth.openart` + `lib.oauth.openart_store`.

DB-free: `lib.brand_secrets.get_secret/set_secret` are replaced with a
dict-backed fake (same no-Postgres philosophy as `test_brand_secrets.py`,
one layer up). The point under test is the regression that took down the
Reels Compose button: a headless process must NEVER reach `input()` -- it
gets a typed `OpenArtAuthRequiredError` -- and a stored-but-expired token
must come back in the shape that forces the MCP provider's silent refresh.
"""
# ruff: noqa: S101, SLF001

from __future__ import annotations

import json
import time
from pathlib import Path

import anyio
import pytest
from mcp.shared.auth import OAuthToken

from lib import brand_secrets
from lib.oauth import openart, openart_store


@pytest.fixture(autouse=True)
def _brand_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    monkeypatch.setenv("PERSONA_BRAND", "b1")
    return tmp_path


@pytest.fixture()
def secrets_store(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    """In-memory stand-in for the encrypted brand_secrets table."""
    store: dict[tuple[str, str], str] = {}

    def _get(brand_id: str, key: str) -> str | None:
        return store.get((brand_id, key))

    def _set(brand_id: str, key: str, value: str) -> bool:
        store[(brand_id, key)] = value
        return True

    monkeypatch.setattr(brand_secrets, "get_secret", _get)
    monkeypatch.setattr(brand_secrets, "set_secret", _set)
    return store


def _token(**overrides: object) -> OAuthToken:
    payload: dict[str, object] = {
        "access_token": "at-secret",
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": "rt-secret",
        "scope": "full_access",
    }
    payload.update(overrides)
    return OAuthToken.model_validate(payload)


# ── headless gating ───────────────────────────────────────────────────────────


def test_non_interactive_redirect_handler_raises_typed_error() -> None:
    handler = openart._make_redirect_handler(interactive=False)
    with pytest.raises(openart.OpenArtAuthRequiredError, match=r"python -m lib\.oauth\.openart"):
        anyio.run(handler, "https://openart.example/authorize")


def test_non_interactive_callback_handler_raises_typed_error() -> None:
    handler = openart._make_callback_handler(interactive=False)
    with pytest.raises(openart.OpenArtAuthRequiredError):
        anyio.run(handler)


def test_provider_defaults_to_non_interactive_without_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The container case: no TTY on stdin -> the consent flow must raise,
    never block on input() (the EOFError this suite regression-tests)."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    provider = openart.build_openart_oauth_provider()
    with pytest.raises(openart.OpenArtAuthRequiredError):
        anyio.run(provider.context.redirect_handler, "https://openart.example/authorize")


def test_interactive_callback_parses_full_redirect_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: "http://localhost:8734/callback?code=abc&state=xyz ",
    )
    handler = openart._make_callback_handler(interactive=True)
    assert anyio.run(handler) == ("abc", "xyz")


def test_extract_auth_required_finds_leaf_in_nested_groups() -> None:
    target = openart.OpenArtAuthRequiredError("auth needed")
    nested = BaseExceptionGroup("outer", [BaseExceptionGroup("inner", [EOFError(), target])])
    assert openart.extract_auth_required(nested) is target
    assert openart.extract_auth_required(BaseExceptionGroup("no-match", [EOFError()])) is None


def test_reauth_message_names_brand_and_cli_command() -> None:
    message = openart.reauth_message("b1")
    assert "b1" in message
    assert "python -m lib.oauth.openart" in message
    assert "Authorize OpenArt" in message


# ── per-brand config gate ─────────────────────────────────────────────────────


def test_openart_enabled_requires_config_flag(tmp_path: Path) -> None:
    assert openart.openart_enabled(tmp_path) is False  # no config.json at all
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"site": {}}))
    assert openart.openart_enabled(tmp_path) is False  # no integrations section
    config.write_text(json.dumps({"integrations": {"openart": {"enabled": False}}}))
    assert openart.openart_enabled(tmp_path) is False
    config.write_text(json.dumps({"integrations": {"openart": {"enabled": True}}}))
    assert openart.openart_enabled(tmp_path) is True
    config.write_text("{not json")
    assert openart.openart_enabled(tmp_path) is False


# ── token storage: expiry-aware, refresh-forcing ──────────────────────────────


def test_fresh_token_round_trips_intact(secrets_store: dict[tuple[str, str], str]) -> None:
    openart_store.save_token_record(_token())
    storage = openart_store.OpenArtTokenStorage()
    loaded = anyio.run(storage.get_tokens)
    assert loaded is not None
    assert loaded.access_token == "at-secret"
    record = json.loads(secrets_store[("b1", openart_store.TOKENS_KEY)])
    assert record["expires_at"] > time.time()


def test_stale_token_comes_back_refresh_forcing(
    secrets_store: dict[tuple[str, str], str],
) -> None:
    """Expired access + live refresh token -> access_token blanked so the MCP
    provider's is_token_valid() goes False and it refreshes silently instead
    of launching the interactive grant (the root cause of the EOFError)."""
    record = {"expires_at": time.time() - 10, "token": _token().model_dump()}
    secrets_store[("b1", openart_store.TOKENS_KEY)] = json.dumps(record)
    loaded = anyio.run(openart_store.OpenArtTokenStorage().get_tokens)
    assert loaded is not None
    assert loaded.access_token == ""
    assert loaded.refresh_token == "rt-secret"


def test_stale_token_without_refresh_token_is_dropped(
    secrets_store: dict[tuple[str, str], str],
) -> None:
    record = {"expires_at": time.time() - 10, "token": _token(refresh_token=None).model_dump()}
    secrets_store[("b1", openart_store.TOKENS_KEY)] = json.dumps(record)
    assert anyio.run(openart_store.OpenArtTokenStorage().get_tokens) is None


def test_legacy_json_token_migrates_additively(
    secrets_store: dict[tuple[str, str], str], _brand_env: Path
) -> None:
    """Pre-DB tokens.json is copied into brand_secrets on first load (age
    unknown -> stale -> refresh-forcing) and the file is NOT deleted."""
    legacy = _brand_env / "state" / "oauth_tokens" / "b1" / "openart" / "tokens.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(_token().model_dump_json())

    loaded = anyio.run(openart_store.OpenArtTokenStorage().get_tokens)
    assert loaded is not None
    assert loaded.access_token == ""  # unknown age -> refresh first
    assert ("b1", openart_store.TOKENS_KEY) in secrets_store
    assert legacy.exists()  # additive migration, never destructive


def test_save_falls_back_to_legacy_file_when_db_unavailable(
    monkeypatch: pytest.MonkeyPatch, _brand_env: Path
) -> None:
    monkeypatch.setattr(brand_secrets, "set_secret", lambda *_a, **_k: False)
    monkeypatch.setattr(brand_secrets, "get_secret", lambda *_a, **_k: None)
    openart_store.save_token_record(_token())
    legacy = _brand_env / "state" / "oauth_tokens" / "b1" / "openart" / "tokens.json"
    assert legacy.exists()


# ── stored_auth_state (compose pre-flight) ────────────────────────────────────


def test_auth_state_missing_without_any_token(secrets_store: dict[tuple[str, str], str]) -> None:
    assert openart_store.stored_auth_state() == "missing"


def test_auth_state_ok_with_refreshable_token(secrets_store: dict[tuple[str, str], str]) -> None:
    record = {"expires_at": time.time() - 10, "token": _token().model_dump()}
    secrets_store[("b1", openart_store.TOKENS_KEY)] = json.dumps(record)
    assert openart_store.stored_auth_state() == "ok"


def test_auth_state_missing_when_expired_and_unrefreshable(
    secrets_store: dict[tuple[str, str], str],
) -> None:
    record = {"expires_at": time.time() - 10, "token": _token(refresh_token=None).model_dump()}
    secrets_store[("b1", openart_store.TOKENS_KEY)] = json.dumps(record)
    assert openart_store.stored_auth_state() == "missing"


# ── silent refresh wiring ─────────────────────────────────────────────────────


def test_provider_is_given_the_real_token_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: `mcp` 1.28 picks the refresh token endpoint from
    `context.oauth_metadata`, and `_initialize()` never sets it when loading a
    STORED token -- so refresh POSTed to `https://mcp.openart.ai/token` and got
    404 ("Token refresh failed: 404", confirmed live), clearing the tokens and
    dropping into the interactive grant. Pre-populating the metadata is what
    makes an expired-but-refreshable token refresh silently."""
    monkeypatch.setattr(openart, "_cached_metadata", None)
    captured: dict[str, str] = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "issuer": "https://openart.ai",
                "authorization_endpoint": "https://openart.ai/suite/api/auth/oauth/authorize",
                "token_endpoint": "https://openart.ai/suite/api/auth/oauth/token",
            }

    def _get(url: str, **_kw: object) -> _Response:
        captured["url"] = url
        return _Response()

    monkeypatch.setattr(openart.httpx, "get", _get)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    provider = openart.build_openart_oauth_provider()

    assert captured["url"].endswith("/.well-known/oauth-authorization-server")
    metadata = provider.context.oauth_metadata
    assert metadata is not None
    assert str(metadata.token_endpoint) == "https://openart.ai/suite/api/auth/oauth/token"


def test_metadata_failure_leaves_provider_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery is best-effort: a network failure must not break provider
    construction (it just falls back to the SDK's own behaviour)."""
    monkeypatch.setattr(openart, "_cached_metadata", None)

    def _boom(*_a: object, **_k: object) -> None:
        raise openart.httpx.ConnectError("no network")

    monkeypatch.setattr(openart.httpx, "get", _boom)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    provider = openart.build_openart_oauth_provider()
    assert provider.context.oauth_metadata is None
