"""Tests for lib.sessions.graph_api."""

from __future__ import annotations

import pytest

from lib.errors import ConfigurationError
from lib.sessions.graph_api import (
    GRAPH_BASE,
    GRAPH_VERSION,
    graph_url,
    read_fb_token,
    read_ig_token,
)


class TestGraphConstants:
    def test_version_is_v23(self) -> None:
        assert GRAPH_VERSION == "v23.0"

    def test_base_includes_version(self) -> None:
        assert GRAPH_BASE == "https://graph.facebook.com/v23.0"


class TestGraphUrl:
    def test_basic(self) -> None:
        assert graph_url("me") == "https://graph.facebook.com/v23.0/me"

    def test_strips_leading_slash(self) -> None:
        assert graph_url("/me") == "https://graph.facebook.com/v23.0/me"

    def test_preserves_query_string(self) -> None:
        assert graph_url("me?fields=id") == "https://graph.facebook.com/v23.0/me?fields=id"


class TestReadFbToken:
    def test_returns_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FB_PAGE_TOKEN", "abc123")
        assert read_fb_token() == "abc123"

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FB_PAGE_TOKEN", "  abc  ")
        assert read_fb_token() == "abc"

    def test_raises_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FB_PAGE_TOKEN", raising=False)
        with pytest.raises(ConfigurationError, match="FB_PAGE_TOKEN"):
            read_fb_token()

    def test_raises_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FB_PAGE_TOKEN", "")
        with pytest.raises(ConfigurationError):
            read_fb_token()


class TestReadIgToken:
    def test_prefers_fb_page_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FB_PAGE_TOKEN", "primary")
        monkeypatch.setenv("IG_GRAPH_ACCESS_TOKEN", "fallback")
        assert read_ig_token() == "primary"

    def test_falls_back_to_legacy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FB_PAGE_TOKEN", raising=False)
        monkeypatch.setenv("IG_GRAPH_ACCESS_TOKEN", "legacy-only")
        assert read_ig_token() == "legacy-only"

    def test_raises_when_neither_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FB_PAGE_TOKEN", raising=False)
        monkeypatch.delenv("IG_GRAPH_ACCESS_TOKEN", raising=False)
        with pytest.raises(ConfigurationError):
            read_ig_token()

    def test_empty_fb_falls_back_to_ig(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FB_PAGE_TOKEN", "")
        monkeypatch.setenv("IG_GRAPH_ACCESS_TOKEN", "ig-token")
        assert read_ig_token() == "ig-token"
