"""Tests for the real `lib.oauth.store.TokenStore` (local JSON-file store).

Supabase was removed as a backend (2026-08): the project's Supabase database
is permanently unreachable, and `TokenStore` never actually used it in
practice -- `_try_init_supabase()` gated on a bare `SUPABASE_KEY` env var
that no credential file in this repo ever defined, so `self._supabase` was
always `None` and every call already fell through to the JSON-file path.

These tests exercise the real class end-to-end (unlike `test_api_oauth.py`,
which monkeypatches a hand-rolled fake `TokenStore`), and guard against a
Supabase backend creeping back in.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lib.oauth.facebook import OAuthToken
from lib.oauth.store import TokenStore


@pytest.fixture(autouse=True)
def _fallback_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `BRAND_DIR` at a tmp_path sandbox -- `_fallback_dir()` reads it
    per call, so setting the env var (not a module attribute) is also a live
    check that the per-call read actually works."""
    monkeypatch.setenv("BRAND_DIR", str(tmp_path))
    return tmp_path / "state" / "oauth_tokens"


def test_no_supabase_module_imported() -> None:
    """The Supabase backend is gone -- constructing/using a store must never
    import the `supabase` package (it may not even be installed anymore)."""
    assert "supabase" not in sys.modules

    store = TokenStore(brand_id="acme-dogs")
    store.save(OAuthToken(access_token="tok", platform="facebook", token_type="page"))
    store.load("facebook", "page")

    assert "supabase" not in sys.modules
    assert not hasattr(store, "_supabase")


def test_save_then_load_round_trips() -> None:
    store = TokenStore(brand_id="acme-dogs")
    token = OAuthToken(
        access_token="secret-token",
        platform="facebook",
        token_type="page",
        token_id="12345",
        scope=["pages_manage_posts"],
    )

    store.save(token)
    loaded = store.load("facebook", "page", "12345")

    assert loaded is not None
    assert loaded.access_token == "secret-token"
    assert loaded.token_id == "12345"
    assert loaded.scope == ["pages_manage_posts"]


def test_load_missing_token_returns_none() -> None:
    store = TokenStore(brand_id="acme-dogs")
    assert store.load("facebook", "page") is None


def test_delete_removes_token() -> None:
    store = TokenStore(brand_id="acme-dogs")
    store.save(OAuthToken(access_token="tok", platform="facebook", token_type="page"))
    assert store.load("facebook", "page") is not None

    store.delete("facebook", "page")

    assert store.load("facebook", "page") is None


def test_delete_missing_token_is_a_noop() -> None:
    store = TokenStore(brand_id="acme-dogs")
    store.delete("facebook", "page")  # must not raise


def test_list_all_returns_redacted_summaries() -> None:
    store = TokenStore(brand_id="acme-dogs")
    store.save(OAuthToken(access_token="tok-1", platform="facebook", token_type="bearer"))
    store.save(
        OAuthToken(access_token="tok-2", platform="instagram", token_type="page", token_id="ig-1")
    )

    summaries = store.list_all()

    assert len(summaries) == 2
    assert all("access_token" not in s for s in summaries)
    platforms = {s["platform"] for s in summaries}
    assert platforms == {"facebook", "instagram"}


def test_list_all_empty_when_no_tokens_saved() -> None:
    store = TokenStore(brand_id="acme-dogs")
    assert store.list_all() == []


def test_fallback_dir_is_read_per_call_not_at_import_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: `_fallback_dir()` must re-read `BRAND_DIR` on every call --
    a module-level read would freeze onto whatever `BRAND_DIR` was set (or
    unset) at first import, silently misplacing tokens for any process that
    imports this module before `load_brand_env_into_environ()` runs."""
    first_brand_dir = tmp_path / "brand-one"
    second_brand_dir = tmp_path / "brand-two"

    monkeypatch.setenv("BRAND_DIR", str(first_brand_dir))
    store = TokenStore(brand_id="acme-dogs")
    store.save(OAuthToken(access_token="tok", platform="facebook", token_type="page"))
    assert (first_brand_dir / "state" / "oauth_tokens" / "acme-dogs").exists()

    monkeypatch.setenv("BRAND_DIR", str(second_brand_dir))
    store.save(OAuthToken(access_token="tok-2", platform="instagram", token_type="page"))
    assert (second_brand_dir / "state" / "oauth_tokens" / "acme-dogs").exists()


def test_stores_are_scoped_per_brand() -> None:
    store_a = TokenStore(brand_id="brand-a")
    store_b = TokenStore(brand_id="brand-b")

    store_a.save(OAuthToken(access_token="a-tok", platform="facebook", token_type="page"))

    assert store_a.load("facebook", "page") is not None
    assert store_b.load("facebook", "page") is None
