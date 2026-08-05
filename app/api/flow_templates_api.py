"""Flow template catalog -- list + edit, backed by `lib/flow_templates_db.py`.

`GET /flow-templates` lists the full pre-brand catalog (the `flow_templates`
table, seeded from `profiles/*.json` by `scripts/backfill_flow_templates.py`)
that `lib/brand_provisioning.py` reads when onboarding a NEW brand.
`PATCH /flow-templates/{id}` edits one row -- it changes what future brands
get provisioned with; it does NOT retroactively touch any already-
provisioned brand's own `schedule_tasks` row (an independent copy once
created -- see `api/approval_api.py`'s `/workers/{label}/schedule` route for
editing an already-provisioned brand's own cron).
"""

from __future__ import annotations

from croniter import croniter
from fastapi import APIRouter, HTTPException

from api.flow_template_schemas import FlowTemplate, FlowTemplateUpdateRequest
from lib import flow_templates_db

router = APIRouter()


@router.get("/flow-templates", response_model=list[FlowTemplate])
def list_flow_templates() -> list[FlowTemplate]:
    """Every flow template, ordered by platform then order_num."""
    return [FlowTemplate(**row) for row in flow_templates_db.load_all()]


@router.patch("/flow-templates/{flow_id}", response_model=FlowTemplate)
def update_flow_template(flow_id: str, body: FlowTemplateUpdateRequest) -> FlowTemplate:
    """Update one flow template's editable fields."""
    existing = flow_templates_db.get(flow_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"No flow template: {flow_id}")

    updates = body.model_dump(exclude_unset=True)
    cron = updates.pop("cron", None)
    if cron is not None:
        cron = cron.strip()
        if not croniter.is_valid(cron):
            raise HTTPException(status_code=400, detail=f"Invalid cron expression: {cron!r}")
        schedule = dict(existing.get("schedule") or {})
        schedule["cron"] = cron
        existing["schedule"] = schedule

    existing.update(updates)
    flow_templates_db.save(existing)

    saved = flow_templates_db.get(flow_id)
    assert saved is not None  # noqa: S101 -- just wrote it, must exist
    return FlowTemplate(**saved)
