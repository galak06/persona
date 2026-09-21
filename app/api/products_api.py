"""Affiliate-products API -- the operator's side of the product catalog.

  GET   /api/v1/products         -- catalog + the focus context to render it
  POST  /api/v1/products         -- add a product
  PATCH /api/v1/products/{key}   -- edit one, or (de)select it for the focus

Storage and validation live in `lib.crew.products.catalog_store`, the focus
rules in `lib.crew.products.focus`, and the wire shapes in
`api.products_schemas`; this module is transport only. It owns exactly one
piece of translation: the `selected` boolean the panel sends is resolved here
against the brand's CURRENT focus category, because only the API can see both
the registry row and the catalog file.

There is no DELETE. Deactivating (`active=false`) is the catalog's delete --
posts published while a product was active still carry its `[AFFILIATE:key]`
placeholder, and the resolver refuses an unknown key, so removing the row
outright would break live pages rather than just future ones.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from api.brand_context import resolve_api_brand
from api.products_schemas import (
    ProductCreate,
    ProductModel,
    ProductsResponse,
    ProductUpdate,
)
from lib import brands_db
from lib.crew.products.catalog_store import (
    CatalogStoreError,
    add_product,
    load_raw,
    set_focus_selection,
    update_product,
)
from lib.crew.products.focus import slugify_category
from lib.crew.products.selector import MAX_PRODUCTS
from lib.observability import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/products", response_model=ProductsResponse)
def get_products() -> ProductsResponse:
    """The active brand's catalog, annotated against its focus category."""
    _brand_id, brand_dir, focus_category = _brand_focus()
    focus_slug = slugify_category(focus_category)
    products = [_to_model(entry, focus_slug) for entry in load_raw(Path(brand_dir))]
    return ProductsResponse(
        focus_category=focus_category,
        focus_slug=focus_slug,
        products=products,
        selected_count=sum(1 for p in products if p.selected and p.active),
        max_products_per_post=MAX_PRODUCTS,
    )


@router.post("/products", response_model=ProductModel, status_code=201)
def post_product(body: ProductCreate) -> ProductModel:
    """Add a product, selected for the current focus unless told otherwise."""
    _brand_id, brand_dir, focus_category = _brand_focus()
    focus_slug = slugify_category(focus_category)
    select_for = focus_category if body.select_for_focus and focus_slug else None
    try:
        entry = add_product(
            Path(brand_dir),
            key=body.key,
            asin=body.asin,
            display=body.display,
            category=body.category or None,
            notes=body.notes or None,
            select_for=select_for,
        )
    except CatalogStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_model(entry, focus_slug)


@router.patch("/products/{key}", response_model=ProductModel)
def patch_product(key: str, body: ProductUpdate) -> ProductModel:
    """Edit one product. `selected` toggles it for the current focus category."""
    _brand_id, brand_dir, focus_category = _brand_focus()
    focus_slug = slugify_category(focus_category)

    if body.selected is not None and not focus_slug:
        raise HTTPException(
            status_code=409,
            detail=(
                "this brand has no focus category, so there is nothing to "
                "select a product for -- set one in Brand Settings first"
            ),
        )

    try:
        entry = _apply_edits(Path(brand_dir), key, body, focus_category)
    except CatalogStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_model(entry, focus_slug)


def _apply_edits(
    brand_dir: Path, key: str, body: ProductUpdate, focus_category: str
) -> dict[str, Any]:
    """Field edits then selection, each inside its own store transaction.

    Selection goes last and its result is what we return, because it is the
    one that can displace another product -- so the entry we hand back has
    been through the exclusivity rule rather than around it.
    """
    entry: dict[str, Any] | None = None
    if any(value is not None for value in (body.display, body.category, body.notes, body.active)):
        entry = update_product(
            brand_dir,
            key,
            display=body.display,
            category=body.category,
            notes=body.notes,
            active=body.active,
        )
    if body.selected is not None:
        entry = set_focus_selection(brand_dir, key, focus_category, selected=body.selected)
    if entry is None:  # an empty PATCH body: report the entry unchanged
        entry = _require(brand_dir, key)
    return entry


def _require(brand_dir: Path, key: str) -> dict[str, Any]:
    """The stored entry for `key`, or a CatalogStoreError the route maps to 404."""
    target = key.strip().lower()
    for entry in load_raw(brand_dir):
        if str(entry.get("key", "")).strip().lower() == target:
            return entry
    raise CatalogStoreError(f"no such product key: {target!r}")


def _brand_focus() -> tuple[str, str, str]:
    """`(brand_id, brand_dir, focus_category)` for the active brand.

    The focus category is read from the registry row rather than the brand's
    config.json for the same reason `lib.ideas_db`'s gate is: the API
    container mounts no BRAND_DIR, so a filesystem lookup would report "no
    focus" here while the pipeline correctly saw one -- the panel would then
    offer selections that silently did nothing.
    """
    brand_id, brand_dir = resolve_api_brand()
    try:
        focus_category = brands_db.focus_category(brand_id)
    except Exception as exc:  # registry unreachable -> render as "no focus"
        logger.warning("products_focus_lookup_failed", brand_id=brand_id, error=str(exc))
        focus_category = ""
    return brand_id, str(brand_dir), focus_category


def _to_model(entry: dict[str, Any], focus_slug: str) -> ProductModel:
    """One stored catalog entry rendered for the panel."""
    raw_tags = entry.get("selected_for") or []
    tags = [str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else []
    category = str(entry.get("category") or "")
    return ProductModel(
        key=str(entry.get("key", "")),
        asin=str(entry.get("asin", "")),
        display=str(entry.get("display") or entry.get("key", "")),
        category=category,
        notes=str(entry.get("notes") or ""),
        active=entry.get("active", True) is not False,
        selected_for=tags,
        selected=bool(focus_slug) and any(slugify_category(tag) == focus_slug for tag in tags),
        in_focus_category=bool(focus_slug) and slugify_category(category) == focus_slug,
    )
