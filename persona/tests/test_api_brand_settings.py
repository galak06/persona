# pyright: reportMissingImports=false
"""Tests for `api/brand_settings_api.py`'s `PATCH /brands/{id}/settings`.

Handler-level unit tests (monkeypatched, no DB) plus one real-Postgres +
real-HTTP round trip, following `test_api_brands.py`/`test_api_brands_live.py`'s
split-by-DB-dependency convention.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from api import brand_settings_api
from fastapi import HTTPException
from fastapi.testclient import TestClient

from lib import brand_provisioning, db
from lib.brand_provisioning import ProvisionResult

_ROW: dict[str, Any] = {
    "id": "acme-dogs",
    "name": "Acme Dogs",
    "site_url": "https://acmedogs.example",
    "niche": "dog nutrition",
    "keywords": {
        "primary_keywords": ["dog food"],
        "secondary_keywords": ["gps"],
        "competitor_mentions": ["brand x"],
    },
    "competitor_accounts": ["@rival1"],
    "headless": True,
    "status": "provisioned",
    "brand_dir": "/brands/acme-dogs",
    "extra": {},
}


def _fake_result(brand_id: str = "acme-dogs") -> ProvisionResult:
    return ProvisionResult(
        brand_id=brand_id,
        brand_dir=Path(f"/brands/{brand_id}"),
        files_written=["config.json", "brand.json"],
        schedule_tasks_created=[f"{brand_id}-ig-scanner", f"{brand_id}-fb-scanner"],
        warnings=[],
    )


def _mock_backend(monkeypatch: pytest.MonkeyPatch, row: dict[str, Any]) -> dict[str, Any]:
    """Monkeypatch `brands_db.get`/`.update` + `provision_brand`, capturing
    the `BrandsRepository.update()` call args for assertions."""
    store = dict(row)
    captured: dict[str, Any] = {}

    def _fake_get(bid: str) -> dict[str, Any] | None:
        return dict(store) if bid == store["id"] else None

    def _fake_update(bid: str, **kwargs: Any) -> bool:
        captured["update_kwargs"] = kwargs
        for key, value in kwargs.items():
            if value is not None:
                store[key] = value
        return True

    monkeypatch.setattr(brand_settings_api.brands_db, "get", _fake_get)
    monkeypatch.setattr(brand_settings_api.brands_db, "update", _fake_update)
    monkeypatch.setattr(
        brand_settings_api, "provision_brand", lambda spec, dry_run=False: _fake_result(store["id"])
    )
    return captured


def test_settings_404_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brand_settings_api.brands_db, "get", lambda _bid: None)
    with pytest.raises(HTTPException) as exc_info:
        brand_settings_api.update_brand_settings(
            "no-such-brand", brand_settings_api.BrandSettingsRequest()
        )
    assert exc_info.value.status_code == 404


def test_settings_headless_only_leaves_keywords_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _mock_backend(monkeypatch, _ROW)
    resp = brand_settings_api.update_brand_settings(
        "acme-dogs", brand_settings_api.BrandSettingsRequest(headless=False)
    )
    assert captured["update_kwargs"]["headless"] is False
    assert captured["update_kwargs"]["keywords"] is None
    assert captured["update_kwargs"]["competitor_accounts"] is None
    assert resp.id == "acme-dogs"


def test_settings_partial_keyword_edit_merges_other_sublists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _mock_backend(monkeypatch, _ROW)
    brand_settings_api.update_brand_settings(
        "acme-dogs",
        brand_settings_api.BrandSettingsRequest(primary_keywords=["new-kw"]),
    )
    merged = captured["update_kwargs"]["keywords"]
    assert merged == {
        "primary_keywords": ["new-kw"],
        "secondary_keywords": ["gps"],  # preserved from the existing row
        "competitor_mentions": ["brand x"],  # preserved from the existing row
    }
    assert captured["update_kwargs"]["headless"] is None


def test_settings_no_fields_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _mock_backend(monkeypatch, _ROW)
    brand_settings_api.update_brand_settings("acme-dogs", brand_settings_api.BrandSettingsRequest())
    assert captured["update_kwargs"] == {
        "headless": None,
        "keywords": None,
        "competitor_accounts": None,
        "enabled_flows": None,
        "group_join_limit": None,
    }


def test_settings_enabled_flows_and_group_join_limit_pass_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _mock_backend(monkeypatch, _ROW)
    brand_settings_api.update_brand_settings(
        "acme-dogs",
        brand_settings_api.BrandSettingsRequest(
            enabled_flows=["ig-scanner", "fb-scanner", "fb-group-scout"],
            group_join_limit=3,
        ),
    )
    assert captured["update_kwargs"]["enabled_flows"] == [
        "ig-scanner",
        "fb-scanner",
        "fb-group-scout",
    ]
    assert captured["update_kwargs"]["group_join_limit"] == 3
    assert captured["update_kwargs"]["headless"] is None
    assert captured["update_kwargs"]["keywords"] is None


def test_settings_provisioning_failure_returns_502(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_backend(monkeypatch, _ROW)

    def _boom(spec: Any, dry_run: bool = False) -> ProvisionResult:
        raise RuntimeError("disk full")

    monkeypatch.setattr(brand_settings_api, "provision_brand", _boom)

    with pytest.raises(HTTPException) as exc_info:
        brand_settings_api.update_brand_settings(
            "acme-dogs", brand_settings_api.BrandSettingsRequest(headless=False)
        )
    assert exc_info.value.status_code == 502


# --------------------------------------------------------------- live + HTTP


def _postgres_reachable() -> bool:
    try:
        return db.health_check()
    except Exception:
        return False


_PG_AVAILABLE = _postgres_reachable()
requires_postgres = pytest.mark.skipif(
    not _PG_AVAILABLE, reason="No reachable Postgres at DATABASE_URL"
)


@pytest.fixture
def pg() -> Iterator[None]:
    schema_path = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
    db.execute(schema_path.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE fb_groups, schedule_tasks, brands CASCADE")


@pytest.fixture
def brands_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(brand_provisioning, "BRANDS_ROOT", tmp_path)
    return tmp_path


@requires_postgres
def test_settings_patch_end_to_end_over_real_http(pg: None, brands_root: Path) -> None:
    from api.approval_api import app

    from tests.test_api_brands import _FULL_BODY

    client = TestClient(app)
    create_resp = client.post("/api/v1/brands", json=_FULL_BODY)
    assert create_resp.status_code == 201

    patch_resp = client.patch(
        "/api/v1/brands/acme-dogs/settings",
        json={
            "headless": False,
            "primary_keywords": ["new-primary"],
            "enabled_flows": ["ig-scanner", "fb-scanner", "fb-group-scout"],
            "group_join_limit": 3,
        },
    )
    assert patch_resp.status_code == 200
    payload = patch_resp.json()
    assert payload["headless"] is False
    assert payload["keywords"]["primary_keywords"] == ["new-primary"]
    # secondary_keywords/competitor_mentions from creation survive the
    # primary_keywords-only PATCH (the merge-not-clobber contract).
    assert payload["keywords"]["secondary_keywords"] == ["gps"]
    assert payload["keywords"]["competitor_mentions"] == ["brand x"]
    assert payload["enabled_flows"] == ["ig-scanner", "fb-scanner", "fb-group-scout"]
    assert payload["group_join_limit"] == 3
    assert "acme-dogs-fb-group-scout" in payload["schedule_tasks_created"]

    brand_json_text = (brands_root / "acme-dogs" / "brand.json").read_text(encoding="utf-8")
    assert '"headless": false' in brand_json_text
    assert '"join_limit_per_day": 3' in brand_json_text

    missing_resp = client.patch("/api/v1/brands/does-not-exist/settings", json={"headless": False})
    assert missing_resp.status_code == 404
