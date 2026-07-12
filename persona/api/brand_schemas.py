"""Pydantic schemas for `api/brands_api.py` (mirrors `api/schedule_schemas.py`'s
split-schemas-from-routes convention).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class BrandCreateRequest(BaseModel):
    """Onboarding-form input. Field names mirror `lib.brand_templates.BrandSpec` 1:1."""

    name: str
    site_url: str
    niche: str
    target_audience: str = ""
    mascot_name: str = ""
    brand_persona: str = ""
    instagram_profile_url: str = ""
    facebook_page_url: str = ""
    primary_keywords: list[str] = []
    secondary_keywords: list[str] = []
    competitor_mentions: list[str] = []
    competitor_accounts: list[str] = []


class BrandSummary(BaseModel):
    """One row's list-view shape (`GET /brands`)."""

    id: str
    name: str
    niche: str
    status: str
    enabled_flows: list[str] = []
    brand_dir: str = ""
    created_at: str = ""


class BrandListResponse(BaseModel):
    brands: list[BrandSummary]
    total: int


class BrandDetail(BaseModel):
    """Full row shape (`GET /brands/{id}`)."""

    id: str
    name: str
    persona: str = ""
    site_url: str = ""
    niche: str = ""
    mascot_name: str = ""
    target_audience: str = ""
    keywords: dict[str, Any] = {}
    competitor_accounts: list[str] = []
    enabled_flows: list[str] = []
    headless: bool = True
    group_join_limit: int = 10
    status: str
    brand_dir: str = ""
    extra: dict[str, Any] = {}
    created_at: str = ""
    updated_at: str = ""


class BrandSettingsRequest(BaseModel):
    """`PATCH /brands/{id}/settings` body. Every field optional and independent --
    an unset field is left untouched (see `BrandsRepository.update`'s own
    `None` = "leave alone" contract, which this mirrors 1:1).
    """

    headless: bool | None = None
    primary_keywords: list[str] | None = None
    secondary_keywords: list[str] | None = None
    competitor_mentions: list[str] | None = None
    competitor_accounts: list[str] | None = None
    enabled_flows: list[str] | None = None
    group_join_limit: int | None = None


class BrandProvisionResponse(BaseModel):
    """Shared success shape for `POST /brands` (201) and `POST /brands/{id}/provision` (200).

    The full `BrandDetail` row shape plus what provisioning did -- matches
    the frontend's `Brand & ProvisionResult` intersection type
    (`frontend/src/api/brands.ts::BrandCreateResponse`) field-for-field.
    """

    # Full brand row (mirrors BrandDetail).
    id: str
    name: str
    persona: str = ""
    site_url: str = ""
    niche: str = ""
    mascot_name: str = ""
    target_audience: str = ""
    keywords: dict[str, Any] = {}
    competitor_accounts: list[str] = []
    enabled_flows: list[str] = []
    headless: bool = True
    group_join_limit: int = 10
    status: str
    brand_dir: str
    extra: dict[str, Any] = {}
    created_at: str = ""
    updated_at: str = ""
    # What provisioning did (mirrors lib.brand_provisioning.ProvisionResult).
    files_written: list[str]
    schedule_tasks_created: list[str]
    warnings: list[str]
    ig_login_command: str
    fb_login_command: str


class FlowLastRun(BaseModel):
    """One `worker_runs` row's shape, as surfaced to the flow-readiness panel."""

    status: str
    last_run: str
    message: str = ""


class FlowReadiness(BaseModel):
    """A flow-specific "is there anything for this flow to do yet" signal."""

    signal: str | None = None
    count: int | None = None
    ready: bool = True
    hint: str = ""


class FlowStatus(BaseModel):
    """One managed flow's full readiness-panel entry (`GET /brands/{id}/flows`)."""

    flow_id: str
    script: str
    enabled: bool
    last_run: FlowLastRun | None = None
    readiness: FlowReadiness


class FlowStatusResponse(BaseModel):
    brand_id: str
    flows: list[FlowStatus]


class RunNowResponse(BaseModel):
    """`POST /brands/{id}/flows/{flow_id}/run` — always enqueues; no cron-due
    or lock check (the equivalent guards `scripts/task_dispatcher.py`
    applies for its own scheduled passes don't apply to an explicit,
    operator-initiated run).
    """

    brand_id: str
    flow_id: str
    schedule_task_id: str
    enqueued: bool = True
