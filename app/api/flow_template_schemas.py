"""Pydantic schemas for `api/flow_templates_api.py` (mirrors
`api/brand_schemas.py`'s split-schemas-from-routes convention).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class FlowTemplate(BaseModel):
    """One row of the pre-brand flow catalog (`flow_templates` table).

    Seeded from `profiles/*.json` by `scripts/backfill_flow_templates.py`;
    `lib/brand_provisioning.py` reads this when onboarding a new brand.
    Editing a row here only affects brands provisioned AFTER the edit --
    an already-provisioned brand's own `schedule_tasks` row is an
    independent copy (see `lib/schedule_db.py`), never retroactively
    touched by a template change.
    """

    id: str
    platform: str
    title: str
    description: str = ""
    order_num: int = 0
    script: str | None = None
    skill: str | None = None
    args: list[str] = []
    depends_on: list[str] = []
    requires_approval: bool = False
    approval_channel: str | None = None
    requires_browser: bool = False
    re_run_guard: bool = True
    output_file: str | None = None
    schedule: dict[str, Any] = {}
    inputs: list[dict[str, Any]] = []
    telegram_notify: bool = True


class FlowTemplateUpdateRequest(BaseModel):
    """Partial update body for `PATCH /flow-templates/{id}`.

    Only fields actually present in the request body are changed
    (`model_dump(exclude_unset=True)`) -- omitting a field leaves it as-is,
    matching `lib.schedule_db.save_task`'s partial-upsert convention.
    """

    description: str | None = None
    script: str | None = None
    cron: str | None = None
    requires_approval: bool | None = None
    requires_browser: bool | None = None
    re_run_guard: bool | None = None
    telegram_notify: bool | None = None
