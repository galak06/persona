"""Tests for `lib.brand_secrets` — encrypted per-brand credentials.

`lib.db` is mocked throughout; no Postgres connection is opened. The crypto
itself is real (Fernet round-trips are cheap and the point of the module).
"""
# ruff: noqa: S101

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from lib import brand_secrets

_KEY = "kJ0Yy3Qm8fVQe0z4rL7xk4nQ0uH8bqZ5t9dW2sYcXpM="  # a valid Fernet key, test-only


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(brand_secrets.MASTER_KEY_ENV, _KEY)


# ── crypto ────────────────────────────────────────────────────────────────


def test_encrypt_decrypt_round_trip() -> None:
    assert brand_secrets.decrypt(brand_secrets.encrypt("EAAG-token")) == "EAAG-token"


def test_ciphertext_does_not_contain_plaintext() -> None:
    """The whole justification for the table: a dump must be useless."""
    token = "EAAGsecrettoken123"
    assert token not in brand_secrets.encrypt(token)


def test_encrypt_is_non_deterministic() -> None:
    """Fernet embeds a random IV, so equal secrets don't produce equal rows."""
    assert brand_secrets.encrypt("same") != brand_secrets.encrypt("same")


def _no_engine_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """Point the app/.env fallback at a nonexistent file. Without this, tests
    that delete the env var still find the developer's real master key in
    app/.env and the no-key paths become untestable."""
    monkeypatch.setattr(brand_secrets, "_ENGINE_ENV_PATH", tmp_path / "no-such.env")  # type: ignore[operator]


def test_missing_master_key_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.delenv(brand_secrets.MASTER_KEY_ENV, raising=False)
    _no_engine_env(monkeypatch, tmp_path)
    with pytest.raises(brand_secrets.SecretsError, match="is not set"):
        brand_secrets.encrypt("x")


def test_master_key_falls_back_to_engine_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """Host scripts have no PERSONA_SECRET_KEY exported; the resolver reads
    THAT ONE KEY from app/.env -- and only that key, never the whole file
    (merging the whole file is the reviewed-and-removed defect that leaked a
    live DATABASE_URL into pytest and bled credentials across brands)."""
    monkeypatch.delenv(brand_secrets.MASTER_KEY_ENV, raising=False)
    env_file = tmp_path / "engine.env"  # type: ignore[operator]
    env_file.write_text(f"DATABASE_URL=postgres://live\n{brand_secrets.MASTER_KEY_ENV}={_KEY}\n")
    monkeypatch.setattr(brand_secrets, "_ENGINE_ENV_PATH", env_file)

    assert brand_secrets.resolve_master_key() == _KEY
    assert brand_secrets.decrypt(brand_secrets.encrypt("v")) == "v"
    import os as _os

    assert "DATABASE_URL" not in [k for k in _os.environ if _os.environ[k] == "postgres://live"]


def test_invalid_master_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(brand_secrets.MASTER_KEY_ENV, "not-a-fernet-key")
    with pytest.raises(brand_secrets.SecretsError, match="not a valid Fernet key"):
        brand_secrets.encrypt("x")


def test_wrong_key_cannot_decrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    token = brand_secrets.encrypt("secret")
    monkeypatch.setenv(brand_secrets.MASTER_KEY_ENV, brand_secrets.generate_master_key())
    with pytest.raises(brand_secrets.SecretsError, match="does not match"):
        brand_secrets.decrypt(token)


def test_generate_master_key_is_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(brand_secrets.MASTER_KEY_ENV, brand_secrets.generate_master_key())
    assert brand_secrets.decrypt(brand_secrets.encrypt("v")) == "v"


# ── storage ───────────────────────────────────────────────────────────────


@patch("lib.brand_secrets.db.execute")
def test_set_secret_stores_ciphertext_not_plaintext(mock_execute: MagicMock) -> None:
    brand_secrets.set_secret("b1", "FB_PAGE_TOKEN", "EAAGplain")
    _query, params = mock_execute.call_args[0]
    assert params[0] == "b1"
    assert params[1] == "FB_PAGE_TOKEN"
    assert "EAAGplain" not in params[2]
    assert brand_secrets.decrypt(params[2]) == "EAAGplain"


@patch("lib.brand_secrets.db.execute")
def test_set_secret_upserts(mock_execute: MagicMock) -> None:
    brand_secrets.set_secret("b1", "K", "v")
    query, _ = mock_execute.call_args[0]
    assert "ON CONFLICT (brand_id, key) DO UPDATE" in query


@patch("lib.brand_secrets.db.fetch_one")
def test_get_secret_decrypts(mock_fetch: MagicMock) -> None:
    mock_fetch.return_value = {"value_enc": brand_secrets.encrypt("EAAGplain")}
    assert brand_secrets.get_secret("b1", "FB_PAGE_TOKEN") == "EAAGplain"


@patch("lib.brand_secrets.db.fetch_one")
def test_get_secret_none_when_absent(mock_fetch: MagicMock) -> None:
    mock_fetch.return_value = None
    assert brand_secrets.get_secret("b1", "NOPE") is None


@patch("lib.brand_secrets.db.fetch_all")
def test_list_keys_returns_names_only(mock_fetch: MagicMock) -> None:
    mock_fetch.return_value = [{"key": "FB_PAGE_TOKEN"}, {"key": "WP_USER"}]
    assert brand_secrets.list_keys("b1") == ["FB_PAGE_TOKEN", "WP_USER"]


@patch("lib.brand_secrets.db.execute")
def test_set_secret_propagates_secrets_error(
    mock_execute: MagicMock, monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """A missing master key must be loud, not swallowed into a False -- a
    silent failure here leaves a publisher on a stale ambient credential."""
    monkeypatch.delenv(brand_secrets.MASTER_KEY_ENV, raising=False)
    _no_engine_env(monkeypatch, tmp_path)
    with pytest.raises(brand_secrets.SecretsError):
        brand_secrets.set_secret("b1", "K", "v")
    mock_execute.assert_not_called()


@patch("lib.brand_secrets.db.execute")
def test_set_secret_refuses_empty_value(mock_execute: MagicMock) -> None:
    """`--set K --value "$UNSET_VAR"` must not store "" -- the loader would
    then apply the empty string over a WORKING .env credential everywhere."""
    with pytest.raises(brand_secrets.SecretsError, match="empty value"):
        brand_secrets.set_secret("b1", "FB_PAGE_TOKEN", "   ")
    mock_execute.assert_not_called()


# ── load_into_environ ─────────────────────────────────────────────────────


def _rows(**secrets: str) -> list[dict[str, str]]:
    """DB rows as the single-query load now fetches them: (key, value_enc)."""
    return [{"key": k, "value_enc": brand_secrets.encrypt(v)} for k, v in secrets.items()]


@patch("lib.brand_secrets.db.fetch_all")
def test_load_overrides_a_stale_ambient_value(
    mock_all: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this module exists for: a dead token exported into the shell
    shadowed the real one because .env loaders skip keys already present."""
    monkeypatch.setenv("FB_PAGE_TOKEN", "STALE-deleted-app-token")
    mock_all.return_value = _rows(FB_PAGE_TOKEN="GOOD-token")

    applied = brand_secrets.load_into_environ("b1")

    assert applied == 1
    assert os.environ["FB_PAGE_TOKEN"] == "GOOD-token"
    assert mock_all.call_count == 1  # ONE query, not 1 + N


@patch("lib.brand_secrets.db.fetch_all")
def test_load_can_defer_when_override_false(
    mock_all: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FB_PAGE_TOKEN", "AMBIENT")
    mock_all.return_value = _rows(FB_PAGE_TOKEN="GOOD-token")

    assert brand_secrets.load_into_environ("b1", override=False) == 0
    assert os.environ["FB_PAGE_TOKEN"] == "AMBIENT"


@patch("lib.brand_secrets.db.fetch_all")
def test_load_is_a_noop_for_a_brand_with_no_secrets(mock_all: MagicMock) -> None:
    """Safe to ship before anything is migrated."""
    mock_all.return_value = []
    assert brand_secrets.load_into_environ("b1") == 0


@patch("lib.brand_secrets.db.fetch_all")
def test_load_is_atomic_one_bad_row_applies_nothing(
    mock_all: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review-confirmed failure mode of the incremental version: applied keys
    up to the bad row, left the rest stale -- a mixed credential set (new
    FB_APP_ID + old FB_PAGE_TOKEN) worse than either source whole. One bad
    row must leave the environment completely untouched and raise, naming it."""
    monkeypatch.setenv("FB_APP_ID", "ambient-app-id")
    monkeypatch.delenv("FB_PAGE_TOKEN", raising=False)
    rows = _rows(FB_APP_ID="db-app-id")
    rows.append({"key": "FB_PAGE_TOKEN", "value_enc": "corrupt-not-fernet"})
    mock_all.return_value = rows

    with pytest.raises(brand_secrets.SecretsError, match="FB_PAGE_TOKEN"):
        brand_secrets.load_into_environ("b1")

    assert os.environ["FB_APP_ID"] == "ambient-app-id"  # NOT overwritten
    assert "FB_PAGE_TOKEN" not in os.environ


def test_managed_keys_are_brand_scoped_only() -> None:
    """Engine-wide keys must NOT be here: stored per brand they would have to
    be duplicated and rotated once per brand."""
    for engine_wide in ("DEEPSEEK_API_KEY", "GEMINI_API_KEY", "SERPER_API_KEY", "DATABASE_URL"):
        assert engine_wide not in brand_secrets.MANAGED_KEYS
    for per_brand in ("FB_PAGE_TOKEN", "IG_ACCOUNT_ID", "WP_APP_PASSWORD"):
        assert per_brand in brand_secrets.MANAGED_KEYS
