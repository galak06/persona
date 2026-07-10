"""Tests for `lib/brands_db/` (the `brands` table via `lib/db.py`).

Real integration tests against a live local Postgres, following
`test_db.py`/`test_groups_db.py`'s skipif pattern -- they run when one is
reachable at `DATABASE_URL` (or `lib.db_pool`'s local dev default) and skip
cleanly otherwise. CI provides a `postgres:16` service container with
`DATABASE_URL` set, so they run for real there.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from lib import db
from lib.brands_db.models import BrandStatus, default_enabled_flows
from lib.brands_db.repository import BrandAlreadyExistsError, BrandsRepository

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def _postgres_reachable() -> bool:
    """Best-effort connectivity probe, used to skip DB tests when none is available."""
    try:
        return db.health_check()
    except Exception:
        return False


_PG_AVAILABLE = _postgres_reachable()
_SKIP_REASON = "No reachable Postgres at DATABASE_URL (or lib.db_pool's local default)"
requires_postgres = pytest.mark.skipif(not _PG_AVAILABLE, reason=_SKIP_REASON)


@pytest.fixture
def repo() -> Iterator[BrandsRepository]:
    """Apply schema.sql (idempotent), yield a fresh repository, truncate after."""
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield BrandsRepository()
    finally:
        db.execute("TRUNCATE TABLE fb_groups, brands CASCADE")


# ------------------------------------------------------------------------- create()


@requires_postgres
def test_create_inserts_row_with_defaults(repo: BrandsRepository) -> None:
    bid = repo.create(
        brand_id="acme-dogs",
        name="Acme Dogs",
        site_url="https://acmedogs.example",
        niche="dog nutrition",
    )
    assert bid == "acme-dogs"

    row = repo.get("acme-dogs")
    assert row is not None
    assert row["name"] == "Acme Dogs"
    assert row["site_url"] == "https://acmedogs.example"
    assert row["niche"] == "dog nutrition"
    assert row["persona"] == ""
    assert row["mascot_name"] == ""
    assert row["target_audience"] == ""
    assert row["keywords"] == {}
    assert row["competitor_accounts"] == []
    assert row["enabled_flows"] == default_enabled_flows()
    assert row["status"] == BrandStatus.DRAFT
    assert row["brand_dir"] == ""
    assert row["extra"] == {}


@requires_postgres
def test_create_accepts_full_field_set(repo: BrandsRepository) -> None:
    bid = repo.create(
        brand_id="full-brand",
        name="Full Brand",
        site_url="https://full.example",
        niche="widgets",
        persona="Widget Wendy",
        mascot_name="Widgy",
        target_audience="DIY makers",
        keywords={"primary_keywords": ["widget"]},
        competitor_accounts=["@rival"],
        enabled_flows=["ig-scanner"],
        status=BrandStatus.PROVISIONING,
        brand_dir="/brands/full-brand",
        extra={"note": "seeded by test"},
    )
    row = repo.get(bid)
    assert row is not None
    assert row["persona"] == "Widget Wendy"
    assert row["mascot_name"] == "Widgy"
    assert row["target_audience"] == "DIY makers"
    assert row["keywords"] == {"primary_keywords": ["widget"]}
    assert row["competitor_accounts"] == ["@rival"]
    assert row["enabled_flows"] == ["ig-scanner"]
    assert row["status"] == BrandStatus.PROVISIONING
    assert row["brand_dir"] == "/brands/full-brand"
    assert row["extra"] == {"note": "seeded by test"}


@requires_postgres
def test_create_duplicate_id_raises(repo: BrandsRepository) -> None:
    repo.create(brand_id="dupe", name="Dupe", site_url="https://dupe.example", niche="x")
    with pytest.raises(BrandAlreadyExistsError):
        repo.create(brand_id="dupe", name="Dupe Again", site_url="https://dupe2.example", niche="x")

    # first row untouched by the failed second insert
    row = repo.get("dupe")
    assert row is not None
    assert row["name"] == "Dupe"


@requires_postgres
@pytest.mark.parametrize(
    "kwargs",
    [
        {"brand_id": "", "name": "N", "site_url": "https://x.example", "niche": "n"},
        {"brand_id": "b", "name": "", "site_url": "https://x.example", "niche": "n"},
        {"brand_id": "b", "name": "N", "site_url": "", "niche": "n"},
        {"brand_id": "b", "name": "N", "site_url": "https://x.example", "niche": ""},
        {"brand_id": "b", "name": "  ", "site_url": "https://x.example", "niche": "n"},
    ],
)
def test_create_missing_required_field_raises(
    repo: BrandsRepository, kwargs: dict[str, str]
) -> None:
    with pytest.raises(ValueError):
        repo.create(**kwargs)


@requires_postgres
def test_create_rejects_invalid_status(repo: BrandsRepository) -> None:
    with pytest.raises(ValueError):
        repo.create(
            brand_id="bad-status",
            name="Bad Status",
            site_url="https://x.example",
            niche="n",
            status="not-a-real-status",
        )


# --------------------------------------------------------------------------- ensure()


@requires_postgres
def test_ensure_is_idempotent_and_upserts_identity_fields(repo: BrandsRepository) -> None:
    bid1 = repo.ensure("dogfoodandfun", "Dog Food & Fun", "Nalla", "https://dogfoodandfun.com")
    bid2 = repo.ensure(
        "dogfoodandfun", "Dog Food & Fun (renamed)", "Nalla", "https://dogfoodandfun.com"
    )
    assert bid1 == bid2 == "dogfoodandfun"

    count_row = db.fetch_one("SELECT count(*) AS n FROM brands")
    assert count_row is not None
    assert count_row["n"] == 1

    row = repo.get("dogfoodandfun")
    assert row is not None
    assert row["name"] == "Dog Food & Fun (renamed)"  # second call's identity wins
    assert row["persona"] == "Nalla"
    assert row["site_url"] == "https://dogfoodandfun.com"


@requires_postgres
def test_ensure_does_not_clobber_onboarding_fields_written_by_create(
    repo: BrandsRepository,
) -> None:
    """ensure() only ever touches {name, persona, site_url} -- niche/status/etc
    written by create() (e.g. re-called from groups_db mid-scan) must survive."""
    repo.create(
        brand_id="onboarded",
        name="Onboarded",
        site_url="https://onboarded.example",
        niche="treats",
        status=BrandStatus.ACTIVE,
    )
    repo.ensure("onboarded", "Onboarded", "", "https://onboarded.example")

    row = repo.get("onboarded")
    assert row is not None
    assert row["niche"] == "treats"
    assert row["status"] == BrandStatus.ACTIVE


@requires_postgres
def test_ensure_defaults_blank_id_to_default(repo: BrandsRepository) -> None:
    bid = repo.ensure("", "Some Name")
    assert bid == "default"


# ----------------------------------------------------------------- get / list_brands


@requires_postgres
def test_get_returns_none_when_missing(repo: BrandsRepository) -> None:
    assert repo.get("no-such-brand") is None


@requires_postgres
def test_list_brands_filters_by_status(repo: BrandsRepository) -> None:
    repo.create(brand_id="draft-one", name="Draft One", site_url="https://d1.example", niche="n")
    repo.create(
        brand_id="active-one",
        name="Active One",
        site_url="https://a1.example",
        niche="n",
        status=BrandStatus.ACTIVE,
    )

    assert {b["id"] for b in repo.list_brands()} == {"draft-one", "active-one"}
    assert [b["id"] for b in repo.list_brands(BrandStatus.ACTIVE)] == ["active-one"]
    assert [b["id"] for b in repo.list_brands(BrandStatus.DRAFT)] == ["draft-one"]
    assert repo.list_brands(BrandStatus.DISABLED) == []


# --------------------------------------------------------------------- update_status


@requires_postgres
def test_update_status_round_trips(repo: BrandsRepository) -> None:
    repo.create(brand_id="statusy", name="Statusy", site_url="https://s.example", niche="n")
    assert repo.update_status("statusy", BrandStatus.PROVISIONED) is True

    row = repo.get("statusy")
    assert row is not None
    assert row["status"] == BrandStatus.PROVISIONED


@requires_postgres
def test_update_status_returns_false_for_missing_brand(repo: BrandsRepository) -> None:
    assert repo.update_status("no-such-brand", BrandStatus.ACTIVE) is False


@requires_postgres
def test_update_status_rejects_invalid_status(repo: BrandsRepository) -> None:
    repo.create(brand_id="statusy2", name="Statusy2", site_url="https://s2.example", niche="n")
    with pytest.raises(ValueError):
        repo.update_status("statusy2", "not-a-real-status")


# -------------------------------------------------------------------- set_brand_dir


@requires_postgres
def test_set_brand_dir_round_trips(repo: BrandsRepository) -> None:
    repo.create(brand_id="dirry", name="Dirry", site_url="https://d.example", niche="n")
    assert repo.set_brand_dir("dirry", "/brands/dirry") is True

    row = repo.get("dirry")
    assert row is not None
    assert row["brand_dir"] == "/brands/dirry"


@requires_postgres
def test_set_brand_dir_returns_false_for_missing_brand(repo: BrandsRepository) -> None:
    assert repo.set_brand_dir("no-such-brand", "/brands/nope") is False


# ------------------------------------------------------------- module-level compat


@requires_postgres
def test_module_level_functions_delegate_to_repository(repo: BrandsRepository) -> None:
    """Sanity check on the `__init__.py` compat layer used by other modules."""
    from lib import brands_db

    bid = brands_db.create(
        brand_id="module-level", name="Module Level", site_url="https://m.example", niche="n"
    )
    assert brands_db.get(bid) is not None
    assert bid in {b["id"] for b in brands_db.list_brands()}
    assert brands_db.update_status(bid, BrandStatus.ACTIVE) is True
    assert brands_db.set_brand_dir(bid, "/brands/module-level") is True
    assert brands_db.get(bid)["brand_dir"] == "/brands/module-level"

    assert brands_db.ensure("legacy-brand", "Legacy") == "legacy-brand"
