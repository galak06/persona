# pyright: reportMissingImports=false
"""Routes for the Jev FB-group gate: the `jev_group_gate` brand setting
(`PATCH /brands/{id}/settings`, `GET /brands/{id}`) and the `fb_group`
platform on `GET /decisions`.

Handler-level tests reuse `test_api_brand_settings.py`'s monkeypatched
backend; the live tests go HTTP -> route -> repository -> real Postgres.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from api import brand_settings_api
from fastapi.testclient import TestClient
from scripts.backfill_flow_templates import load_rows as load_flow_template_rows

from lib import brand_provisioning, brands_db, db, flow_templates_db
from lib.decisions import decisions_db
from lib.decisions.decisions_db import DecisionRecord
from tests._pg import requires_postgres
from tests.test_api_brand_settings import _ROW, _mock_backend


def _client() -> TestClient:
    from api.approval_api import app

    return TestClient(app)


def test_settings_passes_group_gate_through(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _mock_backend(monkeypatch, _ROW)
    resp = brand_settings_api.update_brand_settings(
        "acme-dogs", brand_settings_api.BrandSettingsRequest(jev_group_gate="enforce")
    )
    assert captured["update_kwargs"]["jev_group_gate"] == "enforce"
    assert resp.jev_group_gate == "enforce"


def test_row_without_the_column_reads_as_off(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _mock_backend(monkeypatch, _ROW)  # _ROW predates the column
    resp = brand_settings_api.update_brand_settings(
        "acme-dogs", brand_settings_api.BrandSettingsRequest(headless=False)
    )
    assert captured["update_kwargs"]["jev_group_gate"] is None
    assert resp.jev_group_gate == "off"


def test_group_gate_not_persisted_is_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_backend(monkeypatch, _ROW)
    real_update = brand_settings_api.brands_db.update

    def _drop_group_gate(bid: str, **kwargs: object) -> bool:
        return bool(real_update(bid, **{**kwargs, "jev_group_gate": None}))

    monkeypatch.setattr(brand_settings_api.brands_db, "update", _drop_group_gate)
    resp = brand_settings_api.update_brand_settings(
        "acme-dogs", brand_settings_api.BrandSettingsRequest(jev_group_gate="shadow")
    )
    assert brand_settings_api.JEV_MODES_NOT_SAVED in resp.warnings
    assert resp.jev_group_gate == "off"


def test_unknown_group_gate_mode_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_backend(monkeypatch, _ROW)
    resp = _client().patch("/api/v1/brands/acme-dogs/settings", json={"jev_group_gate": "loud"})
    assert resp.status_code == 422


def test_update_survives_a_db_without_the_gate_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    queries: list[str] = []

    def execute(query: str, params: Any = None) -> int:
        queries.append(query)
        if "jev_group_gate" in query:
            raise psycopg.errors.UndefinedColumn("column jev_group_gate does not exist")
        return 1

    monkeypatch.setattr(db, "execute", execute)
    repo = brands_db.BrandsRepository()
    assert repo.update("acme-dogs", headless=False, jev_group_gate="shadow") is True
    assert "jev_group_gate" not in queries[-1] and "headless" in queries[-1]
    # Only a gate column in the edit: nothing else to save, no exception.
    assert repo.update("acme-dogs", jev_group_gate="shadow") is False
    with pytest.raises(ValueError, match="jev_group_gate"):
        repo.update("acme-dogs", jev_group_gate="loud")


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
        db.execute("TRUNCATE TABLE fb_groups, schedule_tasks, brands, jev_decisions CASCADE")


@requires_postgres
def test_group_gate_round_trip_over_real_http(pg: None) -> None:
    from tests.test_api_brands import _FULL_BODY

    client = _client()
    assert client.post("/api/v1/brands", json=_FULL_BODY).status_code == 201
    assert client.get("/api/v1/brands/acme-dogs").json()["jev_group_gate"] == "shadow"

    resp = client.patch("/api/v1/brands/acme-dogs/settings", json={"jev_group_gate": "off"})
    assert resp.status_code == 200 and resp.json()["jev_group_gate"] == "off"
    row = brands_db.get("acme-dogs")
    assert row is not None and row["jev_group_gate"] == "off"
    assert row["jev_post_gate_ig"] == "shadow"  # the other gates are left alone
    with pytest.raises(Exception, match="jev_group_gate"):
        db.execute("UPDATE brands SET jev_group_gate = 'loud' WHERE id = 'acme-dogs'")


@requires_postgres
def test_decisions_filter_fb_group_live(pg: None) -> None:
    def _rec(platform: str, key: str, would_skip: bool) -> DecisionRecord:
        return DecisionRecord(
            brand_id="b1",
            flow="fb-group-scout" if platform == "fb_group" else "ig-engager",
            platform=platform,
            item_key=key,
            questions={"match": {"type": "choice"}},
            answers={"match": {"choice": "match", "probabilities": {"match": 0.9}}},
            mode="shadow",
            would_skip=would_skip,
            cost_usd=0.001,
        )

    group_url = "https://www.facebook.com/groups/homemadedogfood"
    decisions_db.record_decision(_rec("fb_group", group_url, False))
    decisions_db.record_decision(_rec("fb_group", "https://www.facebook.com/groups/x", True))
    decisions_db.record_decision(_rec("instagram", "https://ig/p/1", True))
    decisions_db.record_outcome(group_url, "joined", brand_id="b1", platform="fb_group")
    decisions_db.record_outcome(
        "https://www.facebook.com/groups/x", "skipped_cap", brand_id="b1", platform="fb_group"
    )

    body = _client().get("/api/v1/decisions?brand_id=b1&platform=fb_group").json()
    assert {d["platform"] for d in body["decisions"]} == {"fb_group"}
    by_key = {d["item_key"]: d for d in body["decisions"]}
    assert by_key[group_url]["agrees"] is True  # kept + joined
    assert by_key["https://www.facebook.com/groups/x"]["agrees"] is None  # budget, not judgement
    assert by_key[group_url]["error"] is None
    assert body["summary"]["total"] == 2
    assert (body["summary"]["compared"], body["summary"]["agreed"]) == (1, 1)
