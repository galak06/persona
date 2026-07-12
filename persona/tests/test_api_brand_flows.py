# pyright: reportMissingImports=false
"""Tests for `api/brand_flows_api.py` (`GET /brands/{id}/flows`,
`POST /brands/{id}/flows/{flow_id}/run`).

Handler-level unit tests (monkeypatched, no DB/Redis) plus one real-Postgres
+ real-Redis + real-HTTP round trip, following `test_api_brands.py`/
`test_api_brands_live.py`'s split-by-dependency convention.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from api import brand_flows_api
from fastapi import HTTPException
from fastapi.testclient import TestClient

from lib import brand_provisioning, db
from lib.task_queue import TaskQueue

_ROW: dict[str, Any] = {
    "id": "acme-dogs",
    "brand_dir": "/brands/acme-dogs",
    "enabled_flows": ["ig-scanner", "fb-scanner"],
}

_TASK_ROW: dict[str, Any] = {
    "id": "acme-dogs-ig-scanner",
    "brand_id": "acme-dogs",
    "script": "scripts/ig_scan.py",
    "args": [],
    "schedule": {"cron": "0 19 * * *"},
}


class _FakeQueue:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.pushed: list[dict[str, Any]] = []
        _FakeQueue.last_instance = self  # type: ignore[attr-defined]

    def push(self, payload: dict[str, Any]) -> str:
        self.pushed.append(payload)
        return "fake-id"


# --------------------------------------------------------------------------- GET .../flows


def test_flow_status_404_when_brand_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brand_flows_api.brands_db, "get", lambda _bid: None)
    with pytest.raises(HTTPException) as exc_info:
        brand_flows_api.get_flow_status("no-such-brand")
    assert exc_info.value.status_code == 404


def test_flow_status_returns_flows_from_lib(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        brand_flows_api.brands_db, "get", lambda bid: dict(_ROW) if bid == "acme-dogs" else None
    )
    monkeypatch.setattr(
        brand_flows_api,
        "flow_status",
        lambda **_kwargs: [
            {
                "flow_id": "ig-scanner",
                "script": "scripts/ig_scan.py",
                "enabled": True,
                "last_run": None,
                "readiness": {"signal": "hashtags", "count": 0, "ready": False, "hint": "x"},
            }
        ],
    )

    resp = brand_flows_api.get_flow_status("acme-dogs")
    assert resp.brand_id == "acme-dogs"
    assert len(resp.flows) == 1
    assert resp.flows[0].flow_id == "ig-scanner"
    assert resp.flows[0].readiness.ready is False


# --------------------------------------------------------------------------- POST .../run


def test_run_now_404_when_brand_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brand_flows_api.brands_db, "get", lambda _bid: None)
    with pytest.raises(HTTPException) as exc_info:
        brand_flows_api.run_flow_now("no-such-brand", "ig-scanner")
    assert exc_info.value.status_code == 404


def test_run_now_404_when_flow_not_provisioned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brand_flows_api.brands_db, "get", lambda _bid: dict(_ROW))
    monkeypatch.setattr(brand_flows_api.schedule_db, "load_all", lambda: [])

    with pytest.raises(HTTPException) as exc_info:
        brand_flows_api.run_flow_now("acme-dogs", "fb-group-scout")
    assert exc_info.value.status_code == 404


def test_run_now_409_when_flow_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {**_ROW, "enabled_flows": ["fb-scanner"]}  # ig-scanner NOT enabled
    monkeypatch.setattr(brand_flows_api.brands_db, "get", lambda _bid: dict(row))
    monkeypatch.setattr(brand_flows_api.schedule_db, "load_all", lambda: [dict(_TASK_ROW)])

    with pytest.raises(HTTPException) as exc_info:
        brand_flows_api.run_flow_now("acme-dogs", "ig-scanner")
    assert exc_info.value.status_code == 409


def test_run_now_enqueues_and_returns_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brand_flows_api.brands_db, "get", lambda _bid: dict(_ROW))
    monkeypatch.setattr(brand_flows_api.schedule_db, "load_all", lambda: [dict(_TASK_ROW)])
    monkeypatch.setattr(brand_flows_api, "TaskQueue", _FakeQueue)

    resp = brand_flows_api.run_flow_now("acme-dogs", "ig-scanner")

    assert resp.brand_id == "acme-dogs"
    assert resp.flow_id == "ig-scanner"
    assert resp.schedule_task_id == "acme-dogs-ig-scanner"
    assert resp.enqueued is True
    pushed = _FakeQueue.last_instance.pushed  # type: ignore[attr-defined]
    assert pushed[0]["script"] == "scripts/ig_scan.py"
    assert pushed[0]["brand"] == "acme-dogs"


# --------------------------------------------------------------- live + HTTP


def _postgres_reachable() -> bool:
    try:
        return db.health_check()
    except Exception:
        return False


def _redis_reachable() -> bool:
    try:
        return TaskQueue(worker="healthcheck", brand="healthcheck").health_check()
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_reachable(), reason="No reachable Postgres at DATABASE_URL"
)
requires_redis = pytest.mark.skipif(
    not _redis_reachable(), reason="No reachable Redis at REDIS_URL"
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
@requires_redis
def test_flows_and_run_now_end_to_end_over_real_http(pg: None, brands_root: Path) -> None:
    from api.approval_api import app

    from tests.test_api_brands import _FULL_BODY

    queue = TaskQueue(worker="flow-run", brand="acme-dogs")
    queue.clear()
    try:
        client = TestClient(app)
        create_resp = client.post("/api/v1/brands", json=_FULL_BODY)
        assert create_resp.status_code == 201

        flows_resp = client.get("/api/v1/brands/acme-dogs/flows")
        assert flows_resp.status_code == 200
        flows = {f["flow_id"]: f for f in flows_resp.json()["flows"]}
        assert flows["ig-scanner"]["enabled"] is True
        assert flows["fb-group-scout"]["enabled"] is False
        assert flows["fb-scanner"]["readiness"]["ready"] is False  # 0 groups joined

        run_resp = client.post("/api/v1/brands/acme-dogs/flows/ig-scanner/run")
        assert run_resp.status_code == 200
        assert run_resp.json()["schedule_task_id"] == "acme-dogs-ig-scanner"
        assert queue.depth() == 1

        # fb-group-scout was never in _FULL_BODY's (default) enabled_flows,
        # so it was never provisioned a schedule_tasks row at all -- 404,
        # not 409 (the disabled-but-provisioned case is covered by the
        # handler-level test_run_now_409_when_flow_disabled).
        never_provisioned_resp = client.post("/api/v1/brands/acme-dogs/flows/fb-group-scout/run")
        assert never_provisioned_resp.status_code == 404

        missing_resp = client.post("/api/v1/brands/does-not-exist/flows/ig-scanner/run")
        assert missing_resp.status_code == 404
    finally:
        queue.clear()
