# pyright: reportMissingImports=false
"""Handler-level unit tests for `api/brands_api.py` (mirrors
`test_engagements_db.py`'s handler-test pattern; monkeypatched, no DB).
See `tests/test_api_brands_live.py` for the real-Postgres + HTTP tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from api import brands_api
from fastapi import HTTPException

from lib.brand_provisioning import ProvisionResult
from lib.brands_db.repository import BrandAlreadyExistsError

_FULL_BODY: dict[str, Any] = {
    "name": "Acme Dogs",
    "site_url": "https://acmedogs.example",
    "niche": "dog nutrition",
    "target_audience": "new dog owners",
    "mascot_name": "Rex",
    "brand_persona": "Rex's Human",
    "instagram_profile_url": "https://instagram.com/acmedogs",
    "facebook_page_url": "https://facebook.com/acmedogs",
    "primary_keywords": ["dog food"],
    "secondary_keywords": ["gps"],
    "competitor_mentions": ["brand x"],
    "competitor_accounts": ["@rival1"],
}


def _fake_result(brand_id: str = "acme-dogs") -> ProvisionResult:
    return ProvisionResult(
        brand_id=brand_id,
        brand_dir=Path(f"/brands/{brand_id}"),
        files_written=[
            "config.json",
            "data/config/brand_facts.md",
            "data/config/instagram_accounts.csv",
        ],
        schedule_tasks_created=[f"{brand_id}-ig-scanner", f"{brand_id}-fb-scanner"],
        warnings=[],
    )


def test_create_brand_success_returns_201_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    created_kwargs: dict[str, Any] = {}

    def _fake_create(**kwargs: Any) -> str:
        created_kwargs.update(kwargs)
        return str(kwargs["brand_id"])

    rows: dict[str, dict[str, Any]] = {}

    def _fake_get(brand_id: str) -> dict[str, Any] | None:
        return rows.get(brand_id)

    monkeypatch.setattr(brands_api.brands_db, "create", _fake_create)
    monkeypatch.setattr(brands_api.brands_db, "get", _fake_get)
    monkeypatch.setattr(
        brands_api, "provision_brand", lambda spec, dry_run=False: _fake_result("acme-dogs")
    )
    # After "provisioning" succeeds, _provision_response() re-fetches the row.
    rows["acme-dogs"] = {
        "name": "Acme Dogs",
        "niche": "dog nutrition",
        "status": "provisioned",
        "mascot_name": "Rex",
        "keywords": {"primary_keywords": ["dog food"]},
        "enabled_flows": ["ig-scanner", "fb-scanner"],
    }

    body = brands_api.BrandCreateRequest(**_FULL_BODY)
    resp = brands_api.create_brand(body)

    assert resp.id == "acme-dogs"
    assert resp.name == "Acme Dogs"
    assert resp.niche == "dog nutrition"
    assert resp.status == "provisioned"
    assert resp.brand_dir == "/brands/acme-dogs"
    assert resp.files_written == _fake_result().files_written
    assert resp.schedule_tasks_created == ["acme-dogs-ig-scanner", "acme-dogs-fb-scanner"]
    assert resp.warnings == []
    assert resp.ig_login_command == "BRAND_DIR=/brands/acme-dogs python scripts/ig_login.py"
    assert resp.fb_login_command == "BRAND_DIR=/brands/acme-dogs python scripts/fb_login.py"
    # Full brand row is included too (matches the frontend's `Brand &
    # ProvisionResult` intersection type) -- not just the ProvisionResult subset.
    assert resp.mascot_name == "Rex"
    assert resp.keywords == {"primary_keywords": ["dog food"]}
    assert resp.enabled_flows == ["ig-scanner", "fb-scanner"]

    # brand_id passed to brands_db.create() is slugify(name), computed by the handler
    assert created_kwargs["brand_id"] == "acme-dogs"
    assert created_kwargs["extra"] == {
        "instagram_profile_url": "https://instagram.com/acmedogs",
        "facebook_page_url": "https://facebook.com/acmedogs",
    }


def test_create_brand_duplicate_name_returns_409(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs: Any) -> str:
        raise BrandAlreadyExistsError("brand 'acme-dogs' already exists")

    monkeypatch.setattr(brands_api.brands_db, "create", _boom)

    body = brands_api.BrandCreateRequest(**_FULL_BODY)
    with pytest.raises(HTTPException) as exc_info:
        brands_api.create_brand(body)
    assert exc_info.value.status_code == 409


def test_create_brand_missing_required_field_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs: Any) -> str:
        raise ValueError("brands.create requires non-empty: site_url")

    monkeypatch.setattr(brands_api.brands_db, "create", _boom)

    body = brands_api.BrandCreateRequest(**{**_FULL_BODY, "site_url": ""})
    with pytest.raises(HTTPException) as exc_info:
        brands_api.create_brand(body)
    assert exc_info.value.status_code == 422


def test_create_brand_provisioning_failure_returns_502_with_retryable_brand_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_create(**kwargs: Any) -> str:
        return str(kwargs["brand_id"])

    def _boom_provision(spec: Any, dry_run: bool = False) -> ProvisionResult:
        raise RuntimeError("disk full")

    monkeypatch.setattr(brands_api.brands_db, "create", _fake_create)
    monkeypatch.setattr(brands_api, "provision_brand", _boom_provision)

    body = brands_api.BrandCreateRequest(**_FULL_BODY)
    with pytest.raises(HTTPException) as exc_info:
        brands_api.create_brand(body)

    assert exc_info.value.status_code == 502
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["brand_id"] == "acme-dogs"
    assert "disk full" in detail["message"]
    assert "acme-dogs" in detail["retry"]


def test_get_brand_404_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brands_api.brands_db, "get", lambda _bid: None)
    with pytest.raises(HTTPException) as exc_info:
        brands_api.get_brand("no-such-brand")
    assert exc_info.value.status_code == 404


def test_get_brand_returns_full_row(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        "id": "acme-dogs",
        "name": "Acme Dogs",
        "persona": "Rex's Human",
        "site_url": "https://acmedogs.example",
        "niche": "dog nutrition",
        "mascot_name": "Rex",
        "target_audience": "new dog owners",
        "keywords": {"primary_keywords": ["dog food"]},
        "competitor_accounts": ["@rival1"],
        "enabled_flows": ["ig-scanner", "fb-scanner"],
        "status": "provisioned",
        "brand_dir": "/brands/acme-dogs",
        "extra": {"instagram_profile_url": "https://instagram.com/acmedogs"},
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-02T00:00:00Z",
    }
    monkeypatch.setattr(
        brands_api.brands_db, "get", lambda bid: row if bid == "acme-dogs" else None
    )

    resp = brands_api.get_brand("acme-dogs")
    assert resp.id == "acme-dogs"
    assert resp.keywords == {"primary_keywords": ["dog food"]}
    assert resp.competitor_accounts == ["@rival1"]
    assert resp.extra == {"instagram_profile_url": "https://instagram.com/acmedogs"}
    assert resp.created_at == "2026-07-01T00:00:00Z"


def _row(id: str, status: str, **extra: Any) -> dict[str, Any]:
    """Minimal `brands` row -- unset optional fields fall back to the
    handler's own `.get(...) or <default>` handling."""
    return {"id": id, "name": id.upper(), "niche": "n", "status": status, **extra}


def test_list_brands_without_status_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_list(status: str | None = None) -> list[dict[str, Any]]:
        captured["status"] = status
        return [
            _row("a", "draft"),
            _row("b", "active", enabled_flows=["ig-scanner"], brand_dir="/brands/b"),
        ]

    monkeypatch.setattr(brands_api.brands_db, "list_brands", _fake_list)
    resp = brands_api.list_brands_endpoint(status_filter=None)
    assert captured["status"] is None
    assert resp.total == 2
    assert [b.id for b in resp.brands] == ["a", "b"]


def test_list_brands_with_status_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_list(status: str | None = None) -> list[dict[str, Any]]:
        captured["status"] = status
        return [_row("b", "active")]

    monkeypatch.setattr(brands_api.brands_db, "list_brands", _fake_list)
    resp = brands_api.list_brands_endpoint(status_filter="active")
    assert captured["status"] == "active"
    assert resp.total == 1
    assert resp.brands[0].status == "active"


def test_reprovision_404_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brands_api.brands_db, "get", lambda _bid: None)
    with pytest.raises(HTTPException) as exc_info:
        brands_api.reprovision_brand("no-such-brand")
    assert exc_info.value.status_code == 404


def test_reprovision_success_rebuilds_spec_from_row_and_reprovisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "id": "acme-dogs",
        "name": "Acme Dogs",
        "persona": "Rex's Human",
        "site_url": "https://acmedogs.example",
        "niche": "dog nutrition",
        "mascot_name": "Rex",
        "target_audience": "new dog owners",
        "keywords": {
            "primary_keywords": ["dog food"],
            "secondary_keywords": ["gps"],
            "competitor_mentions": ["brand x"],
        },
        "competitor_accounts": ["@rival1"],
        "status": "draft",
        "brand_dir": "/brands/acme-dogs",
        "extra": {
            "instagram_profile_url": "https://instagram.com/acmedogs",
            "facebook_page_url": "https://facebook.com/acmedogs",
        },
    }
    monkeypatch.setattr(
        brands_api.brands_db, "get", lambda bid: row if bid == "acme-dogs" else None
    )

    captured_spec: dict[str, Any] = {}

    def _fake_provision(spec: Any, dry_run: bool = False) -> ProvisionResult:
        captured_spec["spec"] = spec
        return _fake_result("acme-dogs")

    monkeypatch.setattr(brands_api, "provision_brand", _fake_provision)

    resp = brands_api.reprovision_brand("acme-dogs")

    spec = captured_spec["spec"]
    assert spec.name == "Acme Dogs"
    assert spec.instagram_profile_url == "https://instagram.com/acmedogs"
    assert spec.facebook_page_url == "https://facebook.com/acmedogs"
    assert spec.primary_keywords == ["dog food"]
    assert spec.competitor_accounts == ["@rival1"]
    assert resp.id == "acme-dogs"
    assert resp.schedule_tasks_created == ["acme-dogs-ig-scanner", "acme-dogs-fb-scanner"]


def test_reprovision_failure_returns_502(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        "id": "acme-dogs",
        "name": "Acme Dogs",
        "site_url": "https://acmedogs.example",
        "niche": "n",
    }
    monkeypatch.setattr(
        brands_api.brands_db, "get", lambda bid: row if bid == "acme-dogs" else None
    )

    def _boom(spec: Any, dry_run: bool = False) -> ProvisionResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(brands_api, "provision_brand", _boom)

    with pytest.raises(HTTPException) as exc_info:
        brands_api.reprovision_brand("acme-dogs")
    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["brand_id"] == "acme-dogs"  # type: ignore[index]
