"""The `(brand_id, brand_dir)` pair every worker-dispatching API route needs.

`reels_compose_api` and `ideas_generate_api` both push `flow-run` items onto
the shared Redis queue, and both must name the brand the way the WORKER will
resolve it: `brand_dir` comes from the `brands` table, never from this
process's own `BRAND_DIR` env var. The worker resolves that path inside ITS
container, so the registered value is the only one both containers agree on.

Shared rather than copied because getting it wrong fails quietly at a
distance: the item is enqueued, the API reports "started", and the run dies in
another container against a path that doesn't exist there.
"""

from __future__ import annotations

from fastapi import HTTPException

from lib import brands_db
from lib.brand_context import current_brand_id


def resolve_api_brand() -> tuple[str, str]:
    """`(brand_id, brand_dir)` for the brand this API serves.

    404 if the brand isn't registered in Postgres; 500 if it is registered but
    carries no `brand_dir` (a provisioning bug -- the worker would have no
    directory to run against).
    """
    brand_id = current_brand_id()
    row = brands_db.get(brand_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"brand '{brand_id}' is not registered")
    brand_dir = str(row.get("brand_dir") or "")
    if not brand_dir:
        raise HTTPException(status_code=500, detail=f"brand '{brand_id}' has no brand_dir")
    return brand_id, brand_dir
