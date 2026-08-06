"""Tests for lib.gsc_client -- no real network/Google API calls.

`fetch_search_analytics` is exercised entirely through a fake
`SearchAnalyticsApi` (two realistic query-response fixtures below), so this
never imports `googleapiclient`/`google.oauth2` at test time.
"""
# ruff: noqa: S101

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from lib.gsc_client import (
    DEFAULT_TARGET_COUNTRIES,
    GoogleSearchAnalyticsApi,
    GscRow,
    _bare_domain,
    _credentials_path_from_env,
    fetch_search_analytics,
    resolve_site_url,
)

_START = date(2026, 5, 1)
_END = date(2026, 7, 30)

# A realistic single-page Search Analytics response.
_ONE_PAGE_RESPONSE: dict[str, Any] = {
    "rows": [
        {
            "keys": ["homemade dog treats", "https://dogfoodandfun.com/homemade-treats/"],
            "position": 8.4,
            "impressions": 320,
            "clicks": 12,
            "ctr": 0.0375,
        },
        {
            "keys": ["pumpkin dog biscuits", "https://dogfoodandfun.com/pumpkin-biscuits/"],
            "position": 14.2,
            "impressions": 55,
            "clicks": 1,
            "ctr": 0.018,
        },
    ]
}


class _FakeApi:
    """Records every `query()` call and returns pre-scripted responses in order."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def query(self, site_url: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((site_url, body))
        if not self._responses:
            return {"rows": []}
        return self._responses.pop(0)


def test_fetch_search_analytics_parses_rows() -> None:
    api = _FakeApi([_ONE_PAGE_RESPONSE])
    rows = fetch_search_analytics(
        "https://dogfoodandfun.com/", _START, _END, row_limit=25_000, api=api
    )
    assert rows == [
        GscRow(
            query="homemade dog treats",
            page="https://dogfoodandfun.com/homemade-treats/",
            position=8.4,
            impressions=320,
            clicks=12,
            ctr=0.0375,
        ),
        GscRow(
            query="pumpkin dog biscuits",
            page="https://dogfoodandfun.com/pumpkin-biscuits/",
            position=14.2,
            impressions=55,
            clicks=1,
            ctr=0.018,
        ),
    ]
    assert len(api.calls) == 1  # a short first page stops pagination
    site_url, body = api.calls[0]
    assert site_url == "https://dogfoodandfun.com/"
    assert body["startDate"] == "2026-05-01"
    assert body["endDate"] == "2026-07-30"
    assert body["dimensions"] == ["query", "page"]


def test_fetch_search_analytics_pages_until_short_response() -> None:
    full_page = {"rows": [_ONE_PAGE_RESPONSE["rows"][0]] * 3}  # 3 rows == row_limit
    short_page = {"rows": [_ONE_PAGE_RESPONSE["rows"][1]]}  # 1 row < row_limit -> stop
    api = _FakeApi([full_page, short_page])
    rows = fetch_search_analytics("https://x.com/", _START, _END, row_limit=3, api=api)
    assert len(rows) == 4
    assert len(api.calls) == 2
    assert api.calls[0][1]["startRow"] == 0
    assert api.calls[1][1]["startRow"] == 3


def test_fetch_search_analytics_respects_max_pages_safety_cap() -> None:
    full_page = {"rows": [_ONE_PAGE_RESPONSE["rows"][0]] * 2}
    api = _FakeApi([full_page, full_page, full_page])  # would page forever without the cap
    rows = fetch_search_analytics("https://x.com/", _START, _END, row_limit=2, max_pages=2, api=api)
    assert len(rows) == 4
    assert len(api.calls) == 2  # stopped by max_pages, not by a short response


def test_fetch_search_analytics_empty_response() -> None:
    api = _FakeApi([{"rows": []}])
    rows = fetch_search_analytics("https://x.com/", _START, _END, api=api)
    assert rows == []


# ── countries: client-side filtering (see fetch_search_analytics docstring for
# why this isn't done via the API's dimensionFilterGroups) ─────────────────


def test_default_target_countries_is_usa_and_canada() -> None:
    assert DEFAULT_TARGET_COUNTRIES == ("usa", "can")


def test_no_countries_arg_is_unchanged_behavior_no_country_dimension() -> None:
    api = _FakeApi([_ONE_PAGE_RESPONSE])
    fetch_search_analytics("https://x.com/", _START, _END, api=api)
    assert api.calls[0][1]["dimensions"] == ["query", "page"]


def test_countries_arg_appends_country_dimension_and_filters() -> None:
    response = {
        "rows": [
            {
                "keys": [
                    "homemade dog treats",
                    "https://dogfoodandfun.com/homemade-treats/",
                    "usa",
                ],
                "position": 8.4,
                "impressions": 320,
                "clicks": 12,
                "ctr": 0.0375,
            },
            {
                "keys": [
                    "homemade dog treats",
                    "https://dogfoodandfun.com/homemade-treats/",
                    "gbr",
                ],
                "position": 9.1,
                "impressions": 50,
                "clicks": 2,
                "ctr": 0.04,
            },
            {
                "keys": [
                    "pumpkin dog biscuits",
                    "https://dogfoodandfun.com/pumpkin-biscuits/",
                    "can",
                ],
                "position": 14.2,
                "impressions": 55,
                "clicks": 1,
                "ctr": 0.018,
            },
        ]
    }
    api = _FakeApi([response])
    rows = fetch_search_analytics("https://x.com/", _START, _END, api=api, countries=("usa", "can"))
    assert api.calls[0][1]["dimensions"] == ["query", "page", "country"]
    assert len(rows) == 2
    assert {r.country for r in rows} == {"usa", "can"}
    assert "gbr" not in {r.country for r in rows}


def test_countries_filter_is_case_insensitive() -> None:
    response = {
        "rows": [
            {
                "keys": ["q", "https://x.com/p", "USA"],
                "position": 1.0,
                "impressions": 1,
                "clicks": 0,
                "ctr": 0.0,
            }
        ]
    }
    api = _FakeApi([response])
    rows = fetch_search_analytics("https://x.com/", _START, _END, api=api, countries=("usa",))
    assert len(rows) == 1


def test_countries_arg_does_not_duplicate_dimension_if_already_present() -> None:
    api = _FakeApi([{"rows": []}])
    fetch_search_analytics(
        "https://x.com/",
        _START,
        _END,
        dimensions=("query", "page", "country"),
        api=api,
        countries=("usa",),
    )
    assert api.calls[0][1]["dimensions"] == ["query", "page", "country"]


def test_row_from_api_country_defaults_to_empty_string_for_two_dimension_query() -> None:
    api = _FakeApi([_ONE_PAGE_RESPONSE])
    rows = fetch_search_analytics("https://x.com/", _START, _END, api=api)
    assert all(r.country == "" for r in rows)
    assert isinstance(rows[0], GscRow)


def test_credentials_path_from_env_requires_the_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_PATH", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_SERVICE_ACCOUNT_PATH"):
        _credentials_path_from_env()


def test_credentials_path_from_env_reads_the_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_PATH", "/tmp/fake-key.json")
    assert _credentials_path_from_env() == "/tmp/fake-key.json"


def test_google_search_analytics_api_raises_on_missing_key_file(tmp_path: Path) -> None:
    """Never a real network call -- fails fast on the credentials file, before
    `googleapiclient`/`google.oauth2` would even be reached."""
    missing = f"{tmp_path}/does-not-exist.json"
    api = GoogleSearchAnalyticsApi(missing)
    with pytest.raises(FileNotFoundError):
        api.query("https://x.com/", {})


# ── resolve_site_url: Domain vs URL-prefix property shape mismatch ─────────


class _FakeSitesApi:
    def __init__(self, site_entries: list[dict[str, Any]]) -> None:
        self._entries = site_entries

    def list(self) -> dict[str, Any]:
        return {"siteEntry": self._entries}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.dogfoodandfun.com/", "dogfoodandfun.com"),
        ("https://dogfoodandfun.com", "dogfoodandfun.com"),
        ("sc-domain:dogfoodandfun.com", "dogfoodandfun.com"),
        ("DogFoodAndFun.com", "dogfoodandfun.com"),
    ],
)
def test_bare_domain_normalizes_all_property_shapes(raw: str, expected: str) -> None:
    assert _bare_domain(raw) == expected


def test_resolve_site_url_matches_domain_property() -> None:
    api = _FakeSitesApi(
        [{"siteUrl": "sc-domain:dogfoodandfun.com", "permissionLevel": "siteRestrictedUser"}]
    )
    assert resolve_site_url("https://dogfoodandfun.com", api=api) == "sc-domain:dogfoodandfun.com"


def test_resolve_site_url_matches_url_prefix_property() -> None:
    api = _FakeSitesApi([{"siteUrl": "https://dogfoodandfun.com/", "permissionLevel": "siteOwner"}])
    assert resolve_site_url("dogfoodandfun.com", api=api) == "https://dogfoodandfun.com/"


def test_resolve_site_url_prefers_domain_property_when_both_registered() -> None:
    api = _FakeSitesApi(
        [
            {"siteUrl": "https://dogfoodandfun.com/", "permissionLevel": "siteOwner"},
            {"siteUrl": "sc-domain:dogfoodandfun.com", "permissionLevel": "siteOwner"},
        ]
    )
    assert resolve_site_url("dogfoodandfun.com", api=api) == "sc-domain:dogfoodandfun.com"


def test_resolve_site_url_raises_with_visible_sites_listed_when_no_match() -> None:
    api = _FakeSitesApi([{"siteUrl": "sc-domain:otherbrand.com", "permissionLevel": "siteOwner"}])
    with pytest.raises(RuntimeError, match=r"otherbrand\.com"):
        resolve_site_url("dogfoodandfun.com", api=api)


def test_resolve_site_url_raises_clearly_when_no_sites_visible() -> None:
    api = _FakeSitesApi([])
    with pytest.raises(RuntimeError, match="no Search Console property"):
        resolve_site_url("dogfoodandfun.com", api=api)
