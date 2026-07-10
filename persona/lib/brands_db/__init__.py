"""Brand registry SQL store (``brands`` table, via ``lib/db.py``).

Module-level functions mirror ``engagements_db``/``groups_db``'s compat-layer
pattern: each opens a short-lived ``BrandsRepository`` and delegates. Use
``BrandsRepository`` directly when several calls need to share one process
(none of these are transactional today, so it rarely matters).

    from lib import brands_db

    brand_id = brands_db.create(name="Acme", site_url="https://acme.example", niche="widgets")
    row = brands_db.get(brand_id)
    brands_db.update_status(brand_id, brands_db.BrandStatus.PROVISIONED)
"""

from __future__ import annotations

from typing import Any

from lib.brands_db.models import BrandStatus, default_enabled_flows
from lib.brands_db.repository import BrandAlreadyExistsError, BrandsRepository

__all__ = [
    "BrandAlreadyExistsError",
    "BrandStatus",
    "BrandsRepository",
    "create",
    "default_enabled_flows",
    "ensure",
    "get",
    "list_brands",
    "set_brand_dir",
    "update_status",
]


def _repo() -> BrandsRepository:
    return BrandsRepository(None)


def create(
    *,
    brand_id: str,
    name: str,
    site_url: str,
    niche: str,
    persona: str = "",
    mascot_name: str = "",
    target_audience: str = "",
    keywords: dict[str, Any] | None = None,
    competitor_accounts: list[Any] | None = None,
    enabled_flows: list[str] | None = None,
    status: str = BrandStatus.DRAFT,
    brand_dir: str = "",
    extra: dict[str, Any] | None = None,
) -> str:
    """Insert a new brand row. Raises on duplicate id or missing required fields."""
    return _repo().create(
        brand_id=brand_id,
        name=name,
        site_url=site_url,
        niche=niche,
        persona=persona,
        mascot_name=mascot_name,
        target_audience=target_audience,
        keywords=keywords,
        competitor_accounts=competitor_accounts,
        enabled_flows=enabled_flows,
        status=status,
        brand_dir=brand_dir,
        extra=extra,
    )


def ensure(brand_id: str, name: str, persona: str = "", site_url: str = "") -> str:
    """Idempotently seed a brand row's identity fields. Returns its id."""
    return _repo().ensure(brand_id, name, persona, site_url)


def get(brand_id: str) -> dict[str, Any] | None:
    return _repo().get(brand_id)


def list_brands(status: str | None = None) -> list[dict[str, Any]]:
    return _repo().list_brands(status)


def update_status(brand_id: str, status: str) -> bool:
    return _repo().update_status(brand_id, status)


def set_brand_dir(brand_id: str, brand_dir: str) -> bool:
    return _repo().set_brand_dir(brand_id, brand_dir)
