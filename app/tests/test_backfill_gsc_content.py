"""Tests for scripts/backfill_gsc_content.py -- pure helper functions only.

`main()` itself (real GSC fetch, real repository writes) is intentionally
NOT exercised here -- see this slice's safety constraints. `GscRow` and the
URL-matching / site-property-resolution logic are pure and fully testable
in isolation.
"""
# ruff: noqa: S101

from __future__ import annotations

import pytest
from scripts import backfill_gsc_content
from scripts.backfill_gsc_content import _resolve_site_property, match_query_rows_to_posts

from lib.gsc_client import GscRow

_ROW_MATCHED = GscRow(
    query="homemade dog treats",
    page="https://dogfoodandfun.com/homemade-treats/",
    position=8.4,
    impressions=320,
    clicks=12,
    ctr=0.0375,
)
_ROW_UNMATCHED = GscRow(
    query="dog food category page",
    page="https://dogfoodandfun.com/category/recipes/",
    position=30.0,
    impressions=10,
    clicks=0,
    ctr=0.0,
)
_RECENT_POSTS = [{"title": "Homemade Treats", "url": "https://dogfoodandfun.com/homemade-treats/"}]


def test_match_query_rows_to_posts_keeps_only_matched_pages() -> None:
    matched = match_query_rows_to_posts([_ROW_MATCHED, _ROW_UNMATCHED], _RECENT_POSTS)
    assert len(matched) == 1
    assert matched[0]["wp_url"] == "https://dogfoodandfun.com/homemade-treats/"
    assert matched[0]["gsc_query"] == "homemade dog treats"
    assert matched[0]["position"] == 8.4
    assert matched[0]["impressions"] == 320


def test_match_query_rows_to_posts_normalizes_trailing_slash() -> None:
    row = GscRow(
        query="homemade dog treats",
        page="https://dogfoodandfun.com/homemade-treats",  # no trailing slash
        position=8.4,
        impressions=320,
        clicks=12,
        ctr=0.0375,
    )
    matched = match_query_rows_to_posts([row], _RECENT_POSTS)  # cached URL HAS a trailing slash
    assert len(matched) == 1


def test_match_query_rows_to_posts_empty_inputs() -> None:
    assert match_query_rows_to_posts([], _RECENT_POSTS) == []
    assert match_query_rows_to_posts([_ROW_MATCHED], []) == []


def test_resolve_site_property_prefers_explicit_override() -> None:
    config = {"site": {"url": "https://dogfoodandfun.com"}}
    assert (
        _resolve_site_property(config, "https://override.example.com/")
        == "https://override.example.com/"
    )


def test_resolve_site_property_delegates_to_resolve_site_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No override -> asks GSC which property shape (Domain vs URL-prefix) is
    actually registered, rather than guessing from the plain config URL."""
    calls: list[str] = []

    def fake_resolve(domain: str) -> str:
        calls.append(domain)
        return "sc-domain:dogfoodandfun.com"

    monkeypatch.setattr(backfill_gsc_content, "resolve_site_url", fake_resolve)
    config = {"site": {"url": "https://dogfoodandfun.com"}}
    assert _resolve_site_property(config, None) == "sc-domain:dogfoodandfun.com"
    assert calls == ["https://dogfoodandfun.com"]


def test_resolve_site_property_raises_when_missing() -> None:
    with pytest.raises(SystemExit):
        _resolve_site_property({"site": {}}, None)


def test_resolve_site_property_wraps_resolution_failure_in_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_resolve(domain: str) -> str:
        raise RuntimeError(f"no Search Console property matching domain '{domain}'")

    monkeypatch.setattr(backfill_gsc_content, "resolve_site_url", fake_resolve)
    config = {"site": {"url": "https://dogfoodandfun.com"}}
    with pytest.raises(SystemExit, match="could not resolve GSC property"):
        _resolve_site_property(config, None)
