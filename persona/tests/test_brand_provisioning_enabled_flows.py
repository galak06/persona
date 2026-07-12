"""Tests for `provision_brand()`'s `enabled_flows`-filtered flow set (PR6).

Split out of `test_brand_provisioning.py` to keep that file under the
project's 300-line cap. Reuses its `pg`/`brands_root`/`requires_postgres`
fixtures and `FULL_SPEC`-style setup.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.brand_provisioning import provision_brand
from lib.brand_templates import BrandSpec
from lib.brands_db.repository import BrandsRepository
from tests.test_brand_provisioning import brands_root, pg, requires_postgres

__all__ = ["brands_root", "pg"]  # re-exported fixtures, used implicitly as test parameters

_ALL_THREE_FLOWS = ["ig-scanner", "fb-scanner", "fb-group-scout"]


def test_dry_run_fb_group_scout_enabled_adds_third_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lib.brand_provisioning as brand_provisioning

    monkeypatch.setattr(brand_provisioning, "BRANDS_ROOT", tmp_path)
    spec = BrandSpec(
        name="Acme Dogs",
        site_url="https://acmedogs.example",
        niche="dog nutrition",
        enabled_flows=_ALL_THREE_FLOWS,
    )
    result = provision_brand(spec, dry_run=True)

    assert set(result.schedule_tasks_created) == {
        "acme-dogs-ig-scanner",
        "acme-dogs-fb-scanner",
        "acme-dogs-fb-group-scout",
    }


@requires_postgres
def test_real_run_fb_group_scout_enabled_creates_third_row(pg: None, brands_root: Path) -> None:
    spec = BrandSpec(
        name="Acme Dogs",
        site_url="https://acmedogs.example",
        niche="dog nutrition",
        enabled_flows=_ALL_THREE_FLOWS,
        group_join_limit=5,
    )
    result = provision_brand(spec, dry_run=False)

    from lib import schedule_db

    rows = {t["id"]: t for t in schedule_db.load_all() if t["brand_id"] == result.brand_id}
    assert "acme-dogs-fb-group-scout" in rows
    assert rows["acme-dogs-fb-group-scout"]["script"] == "scripts/fb_group_scout.py"

    row = BrandsRepository().get(result.brand_id)
    assert row is not None
    assert row["enabled_flows"] == _ALL_THREE_FLOWS
    assert row["group_join_limit"] == 5


@requires_postgres
def test_real_run_disabling_fb_group_scout_does_not_delete_its_row(
    pg: None, brands_root: Path
) -> None:
    """Matches `provision_brand`'s documented contract: re-provisioning with
    a flow removed from `enabled_flows` leaves that flow's existing
    `schedule_tasks` row in place -- `task_dispatcher.py`'s `enabled_flows`
    gate (not row deletion) is what actually stops it from running.

    `provision_brand()` alone never writes `enabled_flows` back to the DB on
    a re-provision (only `create()`, the first-time path, does) -- the real
    settings-edit flow (`api/brand_settings_api.py`) always calls
    `BrandsRepository.update()` first, then rebuilds the `BrandSpec` from
    that just-updated row and re-provisions. This test mirrors that exact
    two-step sequence rather than calling `provision_brand()` in isolation.
    """
    from lib import schedule_db

    enabled_spec = BrandSpec(
        name="Acme Dogs",
        site_url="https://acmedogs.example",
        niche="dog nutrition",
        enabled_flows=_ALL_THREE_FLOWS,
    )
    result = provision_brand(enabled_spec, dry_run=False)

    repo = BrandsRepository()
    repo.update(result.brand_id, enabled_flows=["ig-scanner", "fb-scanner"])
    updated_row = repo.get(result.brand_id)
    assert updated_row is not None
    disabled_spec = BrandSpec(
        name="Acme Dogs",
        site_url="https://acmedogs.example",
        niche="dog nutrition",
        enabled_flows=list(updated_row["enabled_flows"]),
    )
    provision_brand(disabled_spec, dry_run=False)

    rows = {t["id"] for t in schedule_db.load_all() if t["brand_id"] == result.brand_id}
    assert "acme-dogs-fb-group-scout" in rows  # still present, not deleted

    row = repo.get(result.brand_id)
    assert row is not None
    assert row["enabled_flows"] == ["ig-scanner", "fb-scanner"]
