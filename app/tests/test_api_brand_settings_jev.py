# pyright: reportMissingImports=false
"""The Jev post-gate modes on `PATCH /brands/{id}/settings` and `GET /brands/{id}`.

Split from `test_api_brand_settings.py` (300-line cap). Handler-level tests
reuse that module's monkeypatched backend; the live test round-trips the new
`brands` columns through real Postgres and real HTTP.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from api import brand_settings_api
from fastapi.testclient import TestClient
from scripts.backfill_flow_templates import load_rows as load_flow_template_rows

from lib import brand_provisioning, brands_db, db, flow_templates_db
from tests._pg import requires_postgres
from tests.test_api_brand_settings import _ROW, _mock_backend


def test_settings_passes_jev_modes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _mock_backend(monkeypatch, _ROW)
    resp = brand_settings_api.update_brand_settings(
        "acme-dogs",
        brand_settings_api.BrandSettingsRequest(jev_post_gate_ig="enforce", jev_post_gate_fb="off"),
    )
    kwargs = captured["update_kwargs"]
    assert kwargs["jev_post_gate_ig"] == "enforce"
    assert kwargs["jev_post_gate_fb"] == "off"
    assert resp.jev_post_gate_ig == "enforce"
    assert resp.jev_post_gate_fb == "off"


def test_settings_omitted_jev_modes_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _mock_backend(monkeypatch, _ROW)
    resp = brand_settings_api.update_brand_settings(
        "acme-dogs", brand_settings_api.BrandSettingsRequest(headless=False)
    )
    assert captured["update_kwargs"]["jev_post_gate_ig"] is None
    assert captured["update_kwargs"]["jev_post_gate_fb"] is None
    # A row without the columns (pre-migration) reads as "off" -- exactly
    # what the engine does with it -- never as a mode that is not running.
    assert resp.jev_post_gate_ig == "off"


def test_settings_rejects_unknown_mode_with_422(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.approval_api import app

    _mock_backend(monkeypatch, _ROW)
    resp = TestClient(app).patch(
        "/api/v1/brands/acme-dogs/settings", json={"jev_post_gate_ig": "yolo"}
    )
    assert resp.status_code == 422


def test_repository_update_survives_missing_jev_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    """API rebuilt before db/schema.sql: other fields still save, jev ones drop."""
    import psycopg

    from lib.brands_db import repository

    calls: list[dict[str, object]] = []

    def _execute(sql: str, params: dict[str, object]) -> int:
        calls.append(dict(params))
        if "jev_post_gate_ig" in sql:
            raise psycopg.errors.UndefinedColumn("column jev_post_gate_ig does not exist")
        return 1

    monkeypatch.setattr(repository.db, "execute", _execute)
    repo = brands_db.BrandsRepository()
    assert repo.update("acme-dogs", headless=False, jev_post_gate_ig="enforce") is True
    assert calls[-1] == {"headless": False, "id": "acme-dogs"}
    # Only-jev edit against an old DB: nothing left to save, no crash.
    assert repo.update("acme-dogs", jev_post_gate_ig="enforce") is False


def test_repository_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="jev_post_gate_fb"):
        brands_db.BrandsRepository().update("acme-dogs", jev_post_gate_fb="loud")


@pytest.fixture
def pg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    schema_path = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
    db.execute(schema_path.read_text(encoding="utf-8"))
    for row in load_flow_template_rows():
        flow_templates_db.save(row)
    monkeypatch.setattr(brand_provisioning, "BRANDS_ROOT", tmp_path)
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE fb_groups, schedule_tasks, brands CASCADE")


@requires_postgres
def test_jev_modes_round_trip_over_real_http(pg: None) -> None:
    from api.approval_api import app

    from tests.test_api_brands import _FULL_BODY

    client = TestClient(app)
    assert client.post("/api/v1/brands", json=_FULL_BODY).status_code == 201

    # New brands get the column default: shadow on both platforms.
    detail = client.get("/api/v1/brands/acme-dogs").json()
    assert (detail["jev_post_gate_ig"], detail["jev_post_gate_fb"]) == ("shadow", "shadow")

    resp = client.patch("/api/v1/brands/acme-dogs/settings", json={"jev_post_gate_fb": "enforce"})
    assert resp.status_code == 200
    assert resp.json()["jev_post_gate_fb"] == "enforce"
    assert resp.json()["jev_post_gate_ig"] == "shadow"

    row = brands_db.get("acme-dogs")
    assert row is not None
    assert (row["jev_post_gate_ig"], row["jev_post_gate_fb"]) == ("shadow", "enforce")
    # Stored as columns, NOT in config.json -- provision_brand() rewrites that.
    brand_dir = Path(str(row["brand_dir"]))
    assert "jev_post_gate" not in (brand_dir / "config.json").read_text(encoding="utf-8")


@requires_postgres
def test_db_check_constraint_rejects_bad_mode(pg: None) -> None:
    brands_db.ensure("acme-dogs", "Acme Dogs")
    with pytest.raises(Exception, match="jev_post_gate_ig"):
        db.execute("UPDATE brands SET jev_post_gate_ig = 'loud' WHERE id = 'acme-dogs'")
