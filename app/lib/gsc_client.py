"""Google Search Console Search Analytics client, per-brand service-account auth.

Nothing in the repo talks to Google Search Console today (see the plan's
Slice 2 "Auth correction" note) -- this is the first, and it deliberately
uses **service-account** auth, not the three-legged OAuth flow
`lib/oauth/store.py` implements for Facebook. A GCP service account's email
is added as a verified user on each brand's Search Console property (one
manual step per brand in the Search Console UI); no consent screen, no
redirect URI, no token refresh. That property-access grant, plus enabling
the Search Console API on the GCP project, is a human prerequisite this
module cannot do for itself -- see `backfill_gsc_content.py`'s module
docstring for what happens when it hasn't been done yet.

The credentials-file path is read from `GOOGLE_SERVICE_ACCOUNT_PATH`, which
already exists in `lib.brand_env_template.BRAND_ENV_TEMPLATE` (under "Google
Sheets service account") as a per-brand `$BRAND_DIR/.env` key -- reused
as-is, same env-loading convention as `WP_URL`/`WP_USER` in
`lib/sessions/wp_client.py` (`os.environ.get(...)`, populated by
`lib.local_env.load_brand_env_into_environ()` before this module is used).

Testability: `fetch_search_analytics` never imports `googleapiclient` /
`google.oauth2` directly -- it depends only on the `SearchAnalyticsApi`
protocol (one `query()` method). Tests inject a fake; production code gets
`GoogleSearchAnalyticsApi`, which does the real auth + HTTP call and is the
only place those heavy imports happen (lazy, inside `_build_service`, same
pattern as `lib.supabase_client.get_client()`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from lib.observability import get_logger

logger = get_logger(__name__)

_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/webmasters.readonly",)
_DEFAULT_ROW_LIMIT = 25_000
_DEFAULT_MAX_PAGES = 5  # safety cap: 5 * 25,000 = 125k rows per (site, date-range) call


@dataclass(frozen=True)
class GscRow:
    """One Search Analytics result row (`query` x `page`[ x `country`] dimensions)."""

    query: str
    page: str
    position: float
    impressions: int
    clicks: int
    ctr: float
    country: str = ""


class SearchAnalyticsApi(Protocol):
    """The one method `fetch_search_analytics` needs -- inject a fake in tests."""

    def query(self, site_url: str, body: dict[str, Any]) -> dict[str, Any]: ...


def _build_search_console_service(credentials_path: Path) -> Any:
    """Shared service builder -- both the analytics and sites APIs are the
    same `searchconsole` v1 service object, just different resource methods.
    """
    if not credentials_path.is_file():
        raise FileNotFoundError(f"GSC service-account credentials not found: {credentials_path}")
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(credentials_path), scopes=list(_SCOPES)
    )
    return build("searchconsole", "v1", credentials=credentials, cache_discovery=False)


class GoogleSearchAnalyticsApi:
    """Real `searchconsole.searchanalytics().query()` call, service-account auth.

    Never constructed by tests -- they pass a fake `SearchAnalyticsApi`
    directly to `fetch_search_analytics` instead.
    """

    def __init__(self, credentials_path: str | Path) -> None:
        self._credentials_path = Path(credentials_path)
        self._service: Any = None

    def _build_service(self) -> Any:
        if self._service is None:
            self._service = _build_search_console_service(self._credentials_path)
        return self._service

    def query(self, site_url: str, body: dict[str, Any]) -> dict[str, Any]:
        service = self._build_service()
        result: dict[str, Any] = (
            service.searchanalytics().query(siteUrl=site_url, body=body).execute()
        )
        return result


class SitesApi(Protocol):
    """The one method `resolve_site_url` needs -- inject a fake in tests."""

    def list(self) -> dict[str, Any]: ...


class GoogleSitesApi:
    """Real `searchconsole.sites().list()` call -- which properties (domain-
    or URL-prefix-verified) this service account can actually see.
    """

    def __init__(self, credentials_path: str | Path) -> None:
        self._credentials_path = Path(credentials_path)

    def list(self) -> dict[str, Any]:
        service = _build_search_console_service(self._credentials_path)
        result: dict[str, Any] = service.sites().list().execute()
        return result


def _bare_domain(value: str) -> str:
    """Strip scheme/`www.`/`sc-domain:`/trailing-slash down to a bare domain,
    so 'https://www.dogfoodandfun.com/', 'dogfoodandfun.com', and
    'sc-domain:dogfoodandfun.com' all compare equal.
    """
    value = value.strip().lower().removeprefix("sc-domain:")
    value = value.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return value.rstrip("/")


def resolve_site_url(
    domain: str,
    *,
    api: SitesApi | None = None,
    credentials_path: str | Path | None = None,
) -> str:
    """Map a bare brand domain to the exact `siteUrl` string GSC expects.

    Search Console properties come in two incompatible identifier shapes --
    `sc-domain:example.com` (Domain property) or `https://example.com/`
    (URL-prefix property) -- and `searchanalytics.query` 403s if you send
    the wrong one for what's actually registered. Rather than assume a
    shape (config.json only has a plain https URL), ask GSC what this
    service account can actually see and match on the bare domain.
    """
    if api is None:
        api = GoogleSitesApi(credentials_path or _credentials_path_from_env())
    target = _bare_domain(domain)
    entries = api.list().get("siteEntry", []) or []
    matches = [
        str(e["siteUrl"]) for e in entries if _bare_domain(str(e.get("siteUrl", ""))) == target
    ]
    if not matches:
        seen = ", ".join(str(e.get("siteUrl")) for e in entries) or "(none)"
        raise RuntimeError(
            f"no Search Console property matching domain '{domain}' is visible to this "
            f"service account -- has it been added as a user on the property? Visible: {seen}"
        )
    # Prefer a Domain property (sc-domain:) when both shapes are somehow
    # registered -- it's the superset (covers http/https and all subdomains).
    matches.sort(key=lambda s: not s.startswith("sc-domain:"))
    return matches[0]


def _credentials_path_from_env() -> str:
    path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_PATH", "").strip()
    if not path:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_PATH not set -- load the brand .env first "
            "(lib.local_env.load_brand_env_into_environ) or pass credentials_path= explicitly"
        )
    return path


def _row_from_api(raw: dict[str, Any]) -> GscRow:
    """Positional: assumes `dimensions=("query", "page"[, "country"])` -- country,
    when requested, is always the 3rd/last dimension (see `fetch_search_analytics`'s
    `countries` param), so existing `("query", "page")`-only callers are unaffected."""
    keys = raw.get("keys") or ["", ""]
    return GscRow(
        query=str(keys[0]) if len(keys) > 0 else "",
        page=str(keys[1]) if len(keys) > 1 else "",
        position=float(raw.get("position", 0.0)),
        impressions=int(raw.get("impressions", 0)),
        clicks=int(raw.get("clicks", 0)),
        ctr=float(raw.get("ctr", 0.0)),
        country=str(keys[2]) if len(keys) > 2 else "",
    )


#: ISO 3166-1 alpha-3 lowercase, the format GSC's `country` dimension uses.
#: Default target market for this brand (see project memory: "Target market
#: is USA + Canada -- EN-NA brand").
DEFAULT_TARGET_COUNTRIES: tuple[str, ...] = ("usa", "can")


def fetch_search_analytics(
    site_url: str,
    start_date: date,
    end_date: date,
    *,
    dimensions: tuple[str, ...] = ("query", "page"),
    row_limit: int = _DEFAULT_ROW_LIMIT,
    max_pages: int = _DEFAULT_MAX_PAGES,
    countries: tuple[str, ...] | None = None,
    api: SearchAnalyticsApi | None = None,
    credentials_path: str | Path | None = None,
) -> list[GscRow]:
    """Pull Search Analytics rows for `site_url` in `[start_date, end_date]`.

    Pages through the API (`row_limit` rows per call, up to `max_pages`) --
    Search Console caps a single response at 25,000 rows regardless of
    `rowLimit`. Pass `api` to bypass the real network call entirely (tests);
    production callers omit it and get a `GoogleSearchAnalyticsApi` built
    from `credentials_path` (or `GOOGLE_SERVICE_ACCOUNT_PATH`).

    `countries`: ISO 3166-1 alpha-3 lowercase codes (e.g. `("usa", "can")`,
    `DEFAULT_TARGET_COUNTRIES`) to restrict results to. Filtered CLIENT-SIDE
    after fetch, not via the API's `dimensionFilterGroups` -- the OR-across-
    same-dimension filter syntax for "any of several countries" isn't worth
    the risk of a silently-wrong filter (e.g. an empty result set that reads
    as "no data" instead of "bad filter"); result volumes here are small
    enough (low hundreds of rows) that fetching everything and filtering in
    Python is simple, obviously-correct, and easy to test. Implies `country`
    gets added as a query dimension (always last, so existing positional
    `("query", "page")` callers are unaffected -- see `_row_from_api`) if not
    already present in `dimensions`. `None` (default): no country dimension,
    no filtering -- unchanged, pre-existing behavior.
    """
    if api is None:
        api = GoogleSearchAnalyticsApi(credentials_path or _credentials_path_from_env())

    query_dimensions = dimensions
    if countries and "country" not in dimensions:
        query_dimensions = (*dimensions, "country")
    wanted = {c.strip().lower() for c in countries} if countries else None

    rows: list[GscRow] = []
    for page_num in range(max_pages):
        body: dict[str, Any] = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": list(query_dimensions),
            "rowLimit": row_limit,
            "startRow": page_num * row_limit,
        }
        result = api.query(site_url, body)
        raw_rows = result.get("rows") or []
        rows.extend(_row_from_api(r) for r in raw_rows)
        logger.info(
            "gsc_query_page_fetched",
            site_url=site_url,
            page=page_num,
            rows_returned=len(raw_rows),
        )
        if len(raw_rows) < row_limit:
            break

    if wanted is not None:
        rows = [r for r in rows if r.country.lower() in wanted]
    return rows
