"""Brand-directory resolution seam.

`resolve_brand_dir(brand_id=None)` is the single new place code should go to
find a brand's on-disk folder. This is a small, standalone seam -- it does
NOT touch `lib/config.py::load_config()`, which keeps reading `BRAND_DIR`
directly, unchanged, this stage (per the plan: that refactor is explicitly
deferred). The point of adding this seam now, without wiring it into
`load_config()` yet, is so the eventual move to live multi-tenant brand
switching doesn't require inventing this lookup from scratch later.

Two modes:
    resolve_brand_dir()             -- today's behavior, unchanged: reads the
                                        `BRAND_DIR` env var (one process = one
                                        brand, the model every script/API
                                        route already assumes).
    resolve_brand_dir("some-brand") -- looks up `brands.brand_dir` via
                                        `BrandsRepository`, for the future
                                        seam / anything that already knows a
                                        brand id up front (e.g. onboarding's
                                        own post-provision verification).
"""

from __future__ import annotations

import os
from pathlib import Path

from lib.brands_db.repository import BrandsRepository


class BrandNotFoundError(LookupError):
    """Raised when `brand_id` does not match any row in `brands`."""


class BrandDirNotSetError(LookupError):
    """Raised when the brand exists but has no `brand_dir` provisioned yet."""


def resolve_brand_dir(brand_id: str | None = None) -> Path:
    """Resolve a brand's on-disk folder.

    `brand_id=None` reproduces `lib/config.py::load_config()`'s existing
    `BRAND_DIR` handling exactly (same env var, same failure mode: a clear
    error when unset) -- completely unchanged, single-process-per-brand
    behavior. Passing an explicit `brand_id` instead looks it up in the
    `brands` table.
    """
    if brand_id is None:
        raw = os.environ.get("BRAND_DIR")
        if not raw:
            raise ValueError(
                "BRAND_DIR environment variable is not set. "
                "Please set it to the path of the brand configuration directory."
            )
        return Path(raw)

    row = BrandsRepository().get(brand_id)
    if row is None:
        raise BrandNotFoundError(f"no brand registered with id '{brand_id}'")

    brand_dir = row.get("brand_dir") or ""
    if not brand_dir:
        raise BrandDirNotSetError(f"brand '{brand_id}' has no brand_dir set yet")
    return Path(brand_dir)
