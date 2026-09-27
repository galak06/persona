# pyright: reportMissingImports=false
"""Route tests for `GET /api/v1/decisions` (`api/decisions_api.py`).

One monkeypatched test (no DB) pins the response shape and the per-row
`agrees` flag; one live test goes route -> repository -> Postgres.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from api import decisions_api
from fastapi.testclient import TestClient

from lib import db
from lib.decisions import decisions_db
from lib.decisions.decisions_db import DecisionRecord, DecisionSummary
from tests._pg import requires_postgres


def _client() -> TestClient:
    from api.approval_api import app

    return TestClient(app)


def _row(i: int, would_skip: bool, outcome: str | None) -> dict[str, Any]:
    return {
        "id": i,
        "brand_id": "b1",
        "flow": "ig-engager",
        "platform": "instagram",
        "item_key": f"https://x/p/{i}",
        "questions": {},
        "answers": {"relevant": {"noul": 0.4}},
        "mode": "shadow",
        "would_skip": would_skip,
        "latency_ms": 30,
        "cost_usd": 0.001,
        "created_at": datetime(2026, 9, 27, tzinfo=UTC),
        "outcome": outcome,
        "outcome_at": None,
    }


def test_decisions_route_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def _list(**kwargs: Any) -> list[dict[str, Any]]:
        seen["list"] = kwargs
        return [_row(1, True, "declined"), _row(2, True, "engaged"), _row(3, False, None)]

    def _summary(**kwargs: Any) -> DecisionSummary:
        seen["summary"] = kwargs
        return DecisionSummary(
            total=3, would_skip=2, compared=2, agreed=1, agreement_rate=0.5, total_cost_usd=0.003
        )

    monkeypatch.setattr(decisions_api.decisions_db, "list_recent", _list)
    monkeypatch.setattr(decisions_api.decisions_db, "summarize", _summary)

    resp = _client().get("/api/v1/decisions?brand_id=b1&platform=instagram&limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert seen["list"] == {"brand_id": "b1", "platform": "instagram", "limit": 10}
    assert seen["summary"] == {"brand_id": "b1", "platform": "instagram"}
    assert [d["agrees"] for d in body["decisions"]] == [True, False, None]
    assert body["decisions"][0]["created_at"].startswith("2026-09-27T00:00:00")
    assert body["summary"]["agreement_rate"] == 0.5


def test_decisions_route_validates_limit() -> None:
    assert _client().get("/api/v1/decisions?limit=0").status_code == 422


@pytest.fixture
def pg() -> Iterator[None]:
    schema = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
    db.execute(schema.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE jev_decisions")


@requires_postgres
def test_decisions_route_live(pg: None) -> None:
    decisions_db.record_decision(
        DecisionRecord(
            brand_id="b1",
            flow="fb-engager",
            platform="facebook",
            item_key="https://fb/1",
            questions={"unsafe": {"type": "noul"}},
            answers={"unsafe": {"noul": 0.9}},
            mode="shadow",
            would_skip=True,
            latency_ms=50,
            cost_usd=0.002,
        )
    )
    decisions_db.record_outcome("https://fb/1", "declined", brand_id="b1")

    body = _client().get("/api/v1/decisions?brand_id=b1").json()
    assert body["summary"] == {
        "total": 1,
        "would_skip": 1,
        "compared": 1,
        "agreed": 1,
        "agreement_rate": 1.0,
        "total_cost_usd": 0.002,
    }
    only = body["decisions"][0]
    assert only["answers"] == {"unsafe": {"noul": 0.9}}
    assert only["outcome"] == "declined" and only["agrees"] is True
    assert only["outcome_at"]
