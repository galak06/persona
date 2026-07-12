# pyright: reportMissingImports=false
"""Brand settings-edit API — `PATCH /brands/{id}/settings` (split out of
`api/brands_api.py` to keep that file under the project's 300-line limit;
same router/prefix, registered as a second router in `approval_api.py`).

Reuses `brands_api.py`'s private helpers (`_spec_from_row`,
`_provision_response`, `_provisioning_failed_response`) rather than
duplicating them -- one code path rebuilds a `BrandSpec` from a stored row
and re-provisions, shared by create-retry, plain re-provision, and a
settings edit alike.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from api.brand_schemas import BrandProvisionResponse, BrandSettingsRequest
from api.brands_api import _provision_response, _provisioning_failed_response, _spec_from_row
from lib import brands_db
from lib.brand_provisioning import provision_brand

router = APIRouter()


def _merge_keywords(row: dict[str, Any], body: BrandSettingsRequest) -> dict[str, Any] | None:
    """Merge PATCHed keyword sub-lists onto the row's current `keywords` value.

    `keywords` is one JSONB column holding all 3 sub-lists -- PATCHing just
    `primary_keywords` must not clobber `secondary_keywords`/
    `competitor_mentions`, so an update is only built (and the other two
    read back from `row`) when at least one of the 3 is actually present in
    the body. Returns `None` (== "leave `keywords` alone") when none are.
    """
    if (
        body.primary_keywords is None
        and body.secondary_keywords is None
        and body.competitor_mentions is None
    ):
        return None

    existing = dict(row.get("keywords") or {})
    return {
        "primary_keywords": (
            list(body.primary_keywords)
            if body.primary_keywords is not None
            else list(existing.get("primary_keywords") or [])
        ),
        "secondary_keywords": (
            list(body.secondary_keywords)
            if body.secondary_keywords is not None
            else list(existing.get("secondary_keywords") or [])
        ),
        "competitor_mentions": (
            list(body.competitor_mentions)
            if body.competitor_mentions is not None
            else list(existing.get("competitor_mentions") or [])
        ),
    }


@router.patch("/brands/{brand_id}/settings", response_model=BrandProvisionResponse)
def update_brand_settings(brand_id: str, body: BrandSettingsRequest) -> BrandProvisionResponse:
    """Partial settings edit: `headless` + the 4 keyword/competitor lists.

    Every body field is optional and independent. Persists via
    `BrandsRepository.update()`, then re-runs the same rebuild-`BrandSpec`-
    from-row + `provision_brand()` path `POST .../provision` uses -- this is
    what makes a headless toggle or keyword edit actually take effect on the
    next scanner run (`brand.json`/`config.json`/`instagram_accounts.csv`
    all get rewritten), not just stored inertly in Postgres. 404 if the
    brand row doesn't exist.
    """
    row = brands_db.get(brand_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"brand '{brand_id}' not found")

    brands_db.update(
        brand_id,
        headless=body.headless,
        keywords=_merge_keywords(row, body),
        competitor_accounts=(
            list(body.competitor_accounts) if body.competitor_accounts is not None else None
        ),
    )

    updated_row = brands_db.get(brand_id)
    if updated_row is None:  # pragma: no cover -- can't vanish between the get() above and here
        raise HTTPException(status_code=404, detail=f"brand '{brand_id}' not found")

    try:
        result = provision_brand(_spec_from_row(updated_row), dry_run=False)
    except Exception as exc:  # any failure here -> 502, row left as-is (already persisted)
        raise _provisioning_failed_response(brand_id, exc) from exc

    return _provision_response(brand_id, result)
