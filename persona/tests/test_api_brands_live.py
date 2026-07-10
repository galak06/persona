# pyright: reportMissingImports=false
"""Real-Postgres + real-HTTP integration tests for `api/brands_api.py`.

Companion to `tests/test_api_brands.py` (handler-level unit tests, no DB) --
split into its own file to keep both under the project's 300-line cap.

Follows `test_brands_db.py`/`test_brand_provisioning.py`'s skipif pattern --
runs when a Postgres is reachable at `DATABASE_URL` (or `lib.db_pool`'s local
dev default) and skips cleanly otherwise. CI's `postgres:16` service
container makes these run for real there. Provisioning writes to `tmp_path`
(`brand_provisioning.BRANDS_ROOT` monkeypatched), so no real `brands/`
folder is touched and nothing needs manual cleanup.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from api import brands_api
from fastapi import HTTPException
from fastapi.testclient import TestClient

from lib import brand_provisioning, db, schedule_db
from lib.brands_db.repository import BrandsRepository
from tests.test_api_brands import _FULL_BODY

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def _postgres_reachable() -> bool:
    try:
        return db.health_check()
    except Exception:
        return False


_PG_AVAILABLE = _postgres_reachable()
_SKIP_REASON = "No reachable Postgres at DATABASE_URL (or lib.db_pool's local default)"
requires_postgres = pytest.mark.skipif(not _PG_AVAILABLE, reason=_SKIP_REASON)


@pytest.fixture
def pg() -> Iterator[None]:
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE fb_groups, schedule_tasks, brands CASCADE")


@pytest.fixture
def brands_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(brand_provisioning, "BRANDS_ROOT", tmp_path)
    return tmp_path


@requires_postgres
def test_create_get_list_round_trip_via_handlers(pg: None, brands_root: Path) -> None:
    body = brands_api.BrandCreateRequest(**_FULL_BODY)
    created = brands_api.create_brand(body)

    assert created.id == "acme-dogs"
    assert created.status == "provisioned"
    assert (brands_root / "acme-dogs" / "config.json").exists()

    fetched = brands_api.get_brand("acme-dogs")
    assert fetched.name == "Acme Dogs"
    assert fetched.status == "provisioned"
    assert fetched.keywords == {
        "primary_keywords": ["dog food"],
        "secondary_keywords": ["gps"],
        "competitor_mentions": ["brand x"],
    }

    listing = brands_api.list_brands_endpoint(status_filter=None)
    assert "acme-dogs" in {b.id for b in listing.brands}

    listing_filtered = brands_api.list_brands_endpoint(status_filter="provisioned")
    assert "acme-dogs" in {b.id for b in listing_filtered.brands}
    listing_wrong_status = brands_api.list_brands_endpoint(status_filter="draft")
    assert "acme-dogs" not in {b.id for b in listing_wrong_status.brands}

    rows = [t for t in schedule_db.load_all() if t["brand_id"] == "acme-dogs"]
    assert {r["id"] for r in rows} == {"acme-dogs-ig-scanner", "acme-dogs-fb-scanner"}


@requires_postgres
def test_create_duplicate_name_real_409(pg: None, brands_root: Path) -> None:
    brands_api.create_brand(brands_api.BrandCreateRequest(**_FULL_BODY))
    with pytest.raises(HTTPException) as exc_info:
        brands_api.create_brand(brands_api.BrandCreateRequest(**_FULL_BODY))
    assert exc_info.value.status_code == 409

    # duplicate attempt did not touch the original row
    row = BrandsRepository().get("acme-dogs")
    assert row is not None
    assert row["status"] == "provisioned"


@requires_postgres
def test_reprovision_endpoint_is_idempotent_real(pg: None, brands_root: Path) -> None:
    brands_api.create_brand(brands_api.BrandCreateRequest(**_FULL_BODY))
    resp = brands_api.reprovision_brand("acme-dogs")

    assert resp.id == "acme-dogs"
    assert resp.status == "provisioned"

    rows = [t for t in schedule_db.load_all() if t["brand_id"] == "acme-dogs"]
    assert len(rows) == 2  # no duplicate rows from re-provisioning

    brand_rows = [b for b in BrandsRepository().list_brands() if b["id"] == "acme-dogs"]
    assert len(brand_rows) == 1  # no duplicate brand row either


@requires_postgres
def test_create_brand_end_to_end_over_real_http(pg: None, brands_root: Path) -> None:
    """Drives the whole chain over real HTTP through `api.approval_api.app`,
    proving the router wiring in `approval_api.py` (not just the handler
    functions in isolation)."""
    from api.approval_api import app

    client = TestClient(app)
    resp = client.post("/api/v1/brands", json=_FULL_BODY)
    assert resp.status_code == 201
    payload = resp.json()
    assert payload["id"] == "acme-dogs"
    assert payload["status"] == "provisioned"
    assert payload["schedule_tasks_created"] == ["acme-dogs-ig-scanner", "acme-dogs-fb-scanner"]
    assert payload["ig_login_command"].endswith("scripts/ig_login.py")
    assert payload["fb_login_command"].endswith("scripts/fb_login.py")
    # Full brand row (matches the frontend's `Brand & ProvisionResult`
    # intersection type), not just the ProvisionResult subset.
    assert payload["mascot_name"] == "Rex"
    assert payload["keywords"] == {
        "primary_keywords": ["dog food"],
        "secondary_keywords": ["gps"],
        "competitor_mentions": ["brand x"],
    }
    assert payload["enabled_flows"] == ["ig-scanner", "fb-scanner"]

    get_resp = client.get("/api/v1/brands/acme-dogs")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Acme Dogs"

    list_resp = client.get("/api/v1/brands")
    assert list_resp.status_code == 200
    assert "acme-dogs" in {b["id"] for b in list_resp.json()["brands"]}

    dup_resp = client.post("/api/v1/brands", json=_FULL_BODY)
    assert dup_resp.status_code == 409

    missing_resp = client.get("/api/v1/brands/does-not-exist")
    assert missing_resp.status_code == 404

    provision_resp = client.post("/api/v1/brands/acme-dogs/provision")
    assert provision_resp.status_code == 200
    assert provision_resp.json()["status"] == "provisioned"


def test_create_brand_missing_field_returns_422_over_http_without_db() -> None:
    """FastAPI's own Pydantic validation for an absent required field -- no
    DB/provisioning involved, always runs (not gated by `requires_postgres`)."""
    from api.approval_api import app

    client = TestClient(app)
    body = {k: v for k, v in _FULL_BODY.items() if k != "site_url"}
    resp = client.post("/api/v1/brands", json=body)
    assert resp.status_code == 422
