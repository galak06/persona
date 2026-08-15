"""Tests for `api/flow_templates_api.py` (`GET /flow-templates`,
`PATCH /flow-templates/{id}`).

Only covers API-specific behavior not already exercised by
`test_flow_templates_db.py` (the cron-merge logic, validation, error path)
-- plain list/round-trip is the repository layer's job, not this one's.

Real integration tests against a live local Postgres, following
`test_flow_templates_db.py`'s skipif pattern -- they run when one is
reachable at `DATABASE_URL` and skip cleanly otherwise.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from api import flow_templates_api
from api.flow_template_schemas import FlowTemplateUpdateRequest
from fastapi import HTTPException

from lib import db, flow_templates_db

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


from tests._pg import requires_postgres


@pytest.fixture
def pg() -> Iterator[None]:
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    flow_templates_db.save(
        {
            "id": "ig-engager",
            "platform": "instagram",
            "title": "ig-engager",
            "description": "Scan hashtags",
            "order_num": 30,
            "script": "scripts/ig_engager.py",
            "skill": "ig-engager",
            "args": [],
            "depends_on": [],
            "requires_approval": False,
            "approval_channel": None,
            "requires_browser": True,
            "re_run_guard": True,
            "output_file": None,
            "schedule": {"cron": "3 19 * * *", "cadence": "daily"},
            "inputs": [],
            "telegram_notify": True,
        }
    )
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE flow_templates")


@requires_postgres
def test_update_flow_template_changes_cron(pg: None) -> None:
    updated = flow_templates_api.update_flow_template(
        "ig-engager", FlowTemplateUpdateRequest(cron="0 20 * * *")
    )
    assert updated.schedule["cron"] == "0 20 * * *"
    assert updated.schedule["cadence"] == "daily"


@requires_postgres
def test_update_flow_template_rejects_invalid_cron(pg: None) -> None:
    with pytest.raises(HTTPException) as exc_info:
        flow_templates_api.update_flow_template(
            "ig-engager", FlowTemplateUpdateRequest(cron="bogus")
        )
    assert exc_info.value.status_code == 400


@requires_postgres
def test_update_flow_template_404_for_unknown_id(pg: None) -> None:
    with pytest.raises(HTTPException) as exc_info:
        flow_templates_api.update_flow_template(
            "does-not-exist", FlowTemplateUpdateRequest(description="x")
        )
    assert exc_info.value.status_code == 404
