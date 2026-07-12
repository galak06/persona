# pyright: reportMissingImports=false
"""Brand registry + onboarding API (thin router, matches `engagements_api.py`'s style).

``POST /brands`` creates a `brands` row (status=draft) then provisions its
folder + `schedule_tasks` rows via `lib.brand_provisioning.provision_brand`.
If provisioning fails the DB row is left as-is (never deleted) so the client
can retry via ``POST /brands/{id}/provision`` -- the same idempotent path
used to re-provision (e.g. after editing keywords, or for the
dogfoodandfun reset described in the plan).

No delete/deprovision endpoint this stage (deliberate scope cut, see the
plan's "Known limitations"). Schemas live in `api/brand_schemas.py` (mirrors
`api/schedule_schemas.py`'s split-schemas-from-routes convention).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi import status as http_status

from api.brand_schemas import (
    BrandCreateRequest,
    BrandDetail,
    BrandListResponse,
    BrandProvisionResponse,
    BrandSummary,
)
from lib import brands_db
from lib.brand_provisioning import ProvisionResult, provision_brand
from lib.brand_templates import BrandSpec
from lib.brands_db.repository import BrandAlreadyExistsError
from lib.groups_db.models import slugify

router = APIRouter()


# --------------------------------------------------------------------------- helpers


def _spec_from_request(body: BrandCreateRequest) -> BrandSpec:
    return BrandSpec(
        name=body.name,
        site_url=body.site_url,
        niche=body.niche,
        target_audience=body.target_audience,
        mascot_name=body.mascot_name,
        brand_persona=body.brand_persona,
        instagram_profile_url=body.instagram_profile_url,
        facebook_page_url=body.facebook_page_url,
        primary_keywords=list(body.primary_keywords),
        secondary_keywords=list(body.secondary_keywords),
        competitor_mentions=list(body.competitor_mentions),
        competitor_accounts=list(body.competitor_accounts),
    )


def _spec_from_row(row: dict[str, Any]) -> BrandSpec:
    """Reconstruct onboarding input from a stored `brands` row (re-provision path).

    `instagram_profile_url`/`facebook_page_url` have no dedicated columns --
    they're stashed in `extra` at creation time (see `create_brand`) purely
    so this round trip is possible.
    """
    keywords = row.get("keywords") or {}
    extra = row.get("extra") or {}
    return BrandSpec(
        name=str(row.get("name") or ""),
        site_url=str(row.get("site_url") or ""),
        niche=str(row.get("niche") or ""),
        target_audience=str(row.get("target_audience") or ""),
        mascot_name=str(row.get("mascot_name") or ""),
        brand_persona=str(row.get("persona") or ""),
        instagram_profile_url=str(extra.get("instagram_profile_url") or ""),
        facebook_page_url=str(extra.get("facebook_page_url") or ""),
        primary_keywords=list(keywords.get("primary_keywords") or []),
        secondary_keywords=list(keywords.get("secondary_keywords") or []),
        competitor_mentions=list(keywords.get("competitor_mentions") or []),
        competitor_accounts=list(row.get("competitor_accounts") or []),
        headless=bool(row.get("headless", True)),
        enabled_flows=list(row.get("enabled_flows") or brands_db.default_enabled_flows()),
        group_join_limit=int(row.get("group_join_limit") or 10),
    )


def _provision_response(brand_id: str, result: ProvisionResult) -> BrandProvisionResponse:
    """Full stored row (re-fetched post-provision) + what provisioning did."""
    row = brands_db.get(brand_id) or {}
    brand_dir = str(result.brand_dir)
    return BrandProvisionResponse(
        id=brand_id,
        name=str(row.get("name") or ""),
        persona=str(row.get("persona") or ""),
        site_url=str(row.get("site_url") or ""),
        niche=str(row.get("niche") or ""),
        mascot_name=str(row.get("mascot_name") or ""),
        target_audience=str(row.get("target_audience") or ""),
        keywords=dict(row.get("keywords") or {}),
        competitor_accounts=list(row.get("competitor_accounts") or []),
        enabled_flows=list(row.get("enabled_flows") or []),
        headless=bool(row.get("headless", True)),
        group_join_limit=int(row.get("group_join_limit") or 10),
        status=str(row.get("status") or ""),
        brand_dir=brand_dir,
        extra=dict(row.get("extra") or {}),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
        files_written=list(result.files_written),
        schedule_tasks_created=list(result.schedule_tasks_created),
        warnings=list(result.warnings),
        ig_login_command=f"BRAND_DIR={brand_dir} python scripts/ig_login.py",
        fb_login_command=f"BRAND_DIR={brand_dir} python scripts/fb_login.py",
    )


def _provisioning_failed_response(brand_id: str, exc: Exception) -> HTTPException:
    """502 with `brand_id` in the detail -- the retry path is `POST .../provision`.

    The DB row is intentionally left untouched by this function (still
    status=draft from `create_brand`, or whatever it was before a
    `.../provision` retry) -- provisioning failures never delete the row.
    """
    return HTTPException(
        status_code=http_status.HTTP_502_BAD_GATEWAY,
        detail={
            "error": "brand provisioning failed",
            "brand_id": brand_id,
            "message": str(exc),
            "retry": f"POST /api/v1/brands/{brand_id}/provision",
        },
    )


# ----------------------------------------------------------------------------- routes


@router.post(
    "/brands", response_model=BrandProvisionResponse, status_code=http_status.HTTP_201_CREATED
)
def create_brand(body: BrandCreateRequest) -> BrandProvisionResponse:
    """Insert the brand row (status=draft), then provision its folder + schedule rows.

    409 on duplicate slug, 422 on a missing/blank required field (name,
    site_url, niche -- or a name that slugifies to nothing), 502 if
    provisioning itself fails (row is kept at draft for a later retry).
    """
    brand_id = slugify(body.name)

    try:
        brands_db.create(
            brand_id=brand_id,
            name=body.name,
            site_url=body.site_url,
            niche=body.niche,
            persona=body.brand_persona,
            mascot_name=body.mascot_name,
            target_audience=body.target_audience,
            keywords={
                "primary_keywords": list(body.primary_keywords),
                "secondary_keywords": list(body.secondary_keywords),
                "competitor_mentions": list(body.competitor_mentions),
            },
            competitor_accounts=list(body.competitor_accounts),
            extra={
                "instagram_profile_url": body.instagram_profile_url,
                "facebook_page_url": body.facebook_page_url,
            },
        )
    except BrandAlreadyExistsError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    try:
        result = provision_brand(_spec_from_request(body), dry_run=False)
    except Exception as exc:  # any failure here -> 502, row stays at draft
        raise _provisioning_failed_response(brand_id, exc) from exc

    return _provision_response(brand_id, result)


@router.get("/brands", response_model=BrandListResponse)
def list_brands_endpoint(
    status_filter: str | None = Query(default=None, alias="status"),
) -> BrandListResponse:
    """List brands, optionally filtered by `?status=`."""
    rows = brands_db.list_brands(status_filter)
    items = [
        BrandSummary(
            id=str(r.get("id") or ""),
            name=str(r.get("name") or ""),
            niche=str(r.get("niche") or ""),
            status=str(r.get("status") or ""),
            enabled_flows=list(r.get("enabled_flows") or []),
            brand_dir=str(r.get("brand_dir") or ""),
            created_at=str(r.get("created_at") or ""),
        )
        for r in rows
    ]
    return BrandListResponse(brands=items, total=len(items))


@router.get("/brands/{brand_id}", response_model=BrandDetail)
def get_brand(brand_id: str) -> BrandDetail:
    """Full row lookup. 404 if no brand with this id exists."""
    row = brands_db.get(brand_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"brand '{brand_id}' not found")
    return BrandDetail(
        id=str(row.get("id") or ""),
        name=str(row.get("name") or ""),
        persona=str(row.get("persona") or ""),
        site_url=str(row.get("site_url") or ""),
        niche=str(row.get("niche") or ""),
        mascot_name=str(row.get("mascot_name") or ""),
        target_audience=str(row.get("target_audience") or ""),
        keywords=dict(row.get("keywords") or {}),
        competitor_accounts=list(row.get("competitor_accounts") or []),
        enabled_flows=list(row.get("enabled_flows") or []),
        headless=bool(row.get("headless", True)),
        group_join_limit=int(row.get("group_join_limit") or 10),
        status=str(row.get("status") or ""),
        brand_dir=str(row.get("brand_dir") or ""),
        extra=dict(row.get("extra") or {}),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
    )


@router.post("/brands/{brand_id}/provision", response_model=BrandProvisionResponse)
def reprovision_brand(brand_id: str) -> BrandProvisionResponse:
    """Idempotent re-run: rebuilds a `BrandSpec` from the stored row and re-provisions.

    The retry/recovery path for a `POST /brands` that returned 502, and the
    general "re-run onboarding for this brand" path (e.g. after editing
    keywords directly in the DB, or the dogfoodandfun reset). 404 if the
    brand row doesn't exist.
    """
    row = brands_db.get(brand_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"brand '{brand_id}' not found")

    try:
        result = provision_brand(_spec_from_row(row), dry_run=False)
    except Exception as exc:  # any failure here -> 502, row left as-is
        raise _provisioning_failed_response(brand_id, exc) from exc

    return _provision_response(brand_id, result)
