"""Tests for `lib/brand_provisioning.py` (folder + config + schedule_tasks rows).

`dry_run=True` tests still write no files and never touch `brands`/
`schedule_tasks` -- but since the stage-1 flow catalog moved from
`profiles/*.json` to the `flow_templates` table, even a dry run now reads
Postgres to resolve each flow's script/schedule. All tests here (`dry_run`
True or False) are therefore real integration tests against a live local
Postgres, following `test_brands_db.py`/`test_schedule_db.py`'s skipif
pattern -- they run when one is reachable at `DATABASE_URL` and skip cleanly
otherwise; CI's `postgres:16` service container makes them run for real
there.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from scripts.backfill_flow_templates import load_rows as load_flow_template_rows

from lib import brand_provisioning, db, flow_templates_db, schedule_db
from lib.brand_provisioning import ProvisionResult, provision_brand
from lib.brand_templates import BrandSpec
from lib.brands_db.models import BrandStatus
from lib.brands_db.repository import BrandsRepository
from lib.config import AppSettings

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"

FULL_SPEC = BrandSpec(
    name="Acme Dogs",
    site_url="https://acmedogs.example",
    niche="dog nutrition",
    target_audience="new dog owners",
    mascot_name="Rex",
    brand_persona="Rex's Human",
    instagram_profile_url="https://instagram.com/acmedogs",
    facebook_page_url="https://facebook.com/acmedogs",
    primary_keywords=["dog food"],
    secondary_keywords=["gps"],
    competitor_mentions=["brand x"],
    competitor_accounts=["@rival1"],
)


def _postgres_reachable() -> bool:
    try:
        return db.health_check()
    except Exception:
        return False


_PG_AVAILABLE = _postgres_reachable()
_SKIP_REASON = "No reachable Postgres at DATABASE_URL (or lib.db_pool's local default)"
requires_postgres = pytest.mark.skipif(not _PG_AVAILABLE, reason=_SKIP_REASON)


# ---------------------------------------------------------------------- dry_run=True


@pytest.fixture
def pg() -> Iterator[None]:
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    # provision_brand() reads its stage-1 flows (ig-engager/fb-scanner/
    # fb-group-scout) from flow_templates, not profiles/*.json directly --
    # a fresh CI Postgres only gets schema.sql's empty table, so seed it the
    # same way scripts/backfill_flow_templates.py --apply does.
    for row in load_flow_template_rows():
        flow_templates_db.save(row)
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE fb_groups, schedule_tasks, brands CASCADE")


@pytest.fixture
def brands_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(brand_provisioning, "BRANDS_ROOT", tmp_path)
    return tmp_path


@requires_postgres
def test_dry_run_writes_no_files(pg: None, brands_root: Path) -> None:
    provision_brand(FULL_SPEC, dry_run=True)

    assert not (brands_root / "acme-dogs").exists()
    assert list(brands_root.iterdir()) == []


@requires_postgres
def test_dry_run_returns_full_preview(pg: None, brands_root: Path) -> None:
    result = provision_brand(FULL_SPEC, dry_run=True)

    assert isinstance(result, ProvisionResult)
    assert result.brand_id == "acme-dogs"
    assert result.brand_dir == brands_root / "acme-dogs"
    assert set(result.files_written) == {
        "config.json",
        "data/config/brand_facts.md",
        "data/config/instagram_accounts.csv",
        "brand.json",
    }
    assert set(result.schedule_tasks_created) == {"acme-dogs-ig-engager"}
    assert result.warnings == []


@requires_postgres
def test_dry_run_does_not_write_brands_or_schedule_tasks(
    pg: None, brands_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`flow_templates` reads are expected under dry_run now (the catalog
    lives in Postgres), but `brands`/`schedule_tasks` writes are not --
    verified by monkeypatching both to raise if called."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry_run must not write brands/schedule_tasks")

    monkeypatch.setattr(schedule_db, "save_task", _boom)
    monkeypatch.setattr(BrandsRepository, "create", _boom)
    monkeypatch.setattr(BrandsRepository, "get", _boom)

    provision_brand(FULL_SPEC, dry_run=True)  # must not raise


@requires_postgres
def test_dry_run_warns_when_no_keywords_supplied(pg: None, brands_root: Path) -> None:
    bare_spec = BrandSpec(name="Bare Co", site_url="https://bare.example", niche="widgets")

    result = provision_brand(bare_spec, dry_run=True)
    assert len(result.warnings) == 1
    assert "keywords" in result.warnings[0]


@requires_postgres
def test_dry_run_does_not_warn_when_keywords_supplied(pg: None, brands_root: Path) -> None:
    result = provision_brand(FULL_SPEC, dry_run=True)
    assert result.warnings == []


# --------------------------------------------------------------------- dry_run=False


@requires_postgres
def test_real_run_writes_config_json_that_round_trips_through_app_settings(
    pg: None, brands_root: Path
) -> None:
    result = provision_brand(FULL_SPEC, dry_run=False)

    config_path = result.brand_dir / "config.json"
    assert config_path.exists()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    settings = AppSettings(**data)
    assert settings.site.name == "Acme Dogs"


@requires_postgres
def test_real_run_writes_all_four_files(pg: None, brands_root: Path) -> None:
    result = provision_brand(FULL_SPEC, dry_run=False)

    assert (result.brand_dir / "config.json").exists()
    assert (result.brand_dir / "data" / "config" / "brand_facts.md").exists()
    assert (result.brand_dir / "data" / "config" / "instagram_accounts.csv").exists()
    assert (result.brand_dir / "brand.json").exists()


@requires_postgres
def test_real_run_writes_brand_json_defaulting_headless_true(pg: None, brands_root: Path) -> None:
    result = provision_brand(FULL_SPEC, dry_run=False)

    data = json.loads((result.brand_dir / "brand.json").read_text(encoding="utf-8"))
    assert data["runtime"] == {"headless": True}


@requires_postgres
def test_real_run_writes_brand_json_reflecting_headless_false(pg: None, brands_root: Path) -> None:
    spec = BrandSpec(
        name="Acme Dogs",
        site_url="https://acmedogs.example",
        niche="dog nutrition",
        headless=False,
    )
    result = provision_brand(spec, dry_run=False)

    data = json.loads((result.brand_dir / "brand.json").read_text(encoding="utf-8"))
    assert data["runtime"] == {"headless": False}


@requires_postgres
def test_real_run_creates_only_the_three_scoped_directories(pg: None, brands_root: Path) -> None:
    result = provision_brand(FULL_SPEC, dry_run=False)

    assert (result.brand_dir / "data" / "config").is_dir()
    assert (result.brand_dir / "state").is_dir()
    assert (result.brand_dir / "logs").is_dir()
    # No data/db/ -- everything is Postgres now.
    assert not (result.brand_dir / "data" / "db").exists()
    # No lazily-created session/queue/dedup files yet -- those are ig_engager.py/
    # fb_scan.py's job on first run, not provisioning's.
    assert not (result.brand_dir / "state" / "instagram_session.json").exists()


@requires_postgres
def test_real_run_inserts_exactly_one_schedule_tasks_row(pg: None, brands_root: Path) -> None:
    result = provision_brand(FULL_SPEC, dry_run=False)

    rows = [t for t in schedule_db.load_all() if t["brand_id"] == result.brand_id]
    assert {r["id"] for r in rows} == {"acme-dogs-ig-engager"}
    for row in rows:
        assert row["requires_browser"] == 1
        assert row["schedule"]["cron"]


@requires_postgres
def test_real_run_schedule_task_scripts_match_the_flow(pg: None, brands_root: Path) -> None:
    result = provision_brand(FULL_SPEC, dry_run=False)

    rows = {t["id"]: t for t in schedule_db.load_all() if t["brand_id"] == result.brand_id}
    assert rows["acme-dogs-ig-engager"]["script"] == "scripts/ig_engager.py"


@requires_postgres
def test_real_run_creates_brand_row_provisioned_with_brand_dir_set(
    pg: None, brands_root: Path
) -> None:
    result = provision_brand(FULL_SPEC, dry_run=False)

    row = BrandsRepository().get(result.brand_id)
    assert row is not None
    assert row["status"] == BrandStatus.PROVISIONED
    assert row["brand_dir"] == str(result.brand_dir)
    assert row["name"] == "Acme Dogs"
    assert row["keywords"] == {
        "primary_keywords": ["dog food"],
        "secondary_keywords": ["gps"],
        "competitor_mentions": ["brand x"],
    }
    assert row["competitor_accounts"] == ["@rival1"]


@requires_postgres
def test_real_run_is_idempotent_no_duplicate_rows_no_crash(pg: None, brands_root: Path) -> None:
    provision_brand(FULL_SPEC, dry_run=False)
    provision_brand(FULL_SPEC, dry_run=False)  # must not raise

    rows = [t for t in schedule_db.load_all() if t["brand_id"] == "acme-dogs"]
    assert len(rows) == 1

    brand_rows = [b for b in BrandsRepository().list_brands() if b["id"] == "acme-dogs"]
    assert len(brand_rows) == 1


@requires_postgres
def test_real_run_idempotent_rerun_preserves_manually_updated_status(
    pg: None, brands_root: Path
) -> None:
    """Re-provisioning must not clobber a status change made in between runs
    with a stale 'draft' default from a second create() attempt."""
    provision_brand(FULL_SPEC, dry_run=False)
    BrandsRepository().update_status("acme-dogs", BrandStatus.ACTIVE)

    provision_brand(FULL_SPEC, dry_run=False)

    row = BrandsRepository().get("acme-dogs")
    assert row is not None
    # provision_brand always re-asserts PROVISIONED per the plan's step (e) --
    # this documents that behavior rather than silently relying on it.
    assert row["status"] == BrandStatus.PROVISIONED


@requires_postgres
def test_real_run_preserves_hand_curated_instagram_accounts_csv(
    pg: None, brands_root: Path
) -> None:
    """Re-provisioning must never clobber an existing instagram_accounts.csv --
    it may hold hand-curated hashtags no keyword mechanically derives (this
    regressed live: enabling a flow via a settings edit wiped a real brand's
    26-hashtag file down to its header row, see lib/brand_provisioning.py)."""
    provision_brand(FULL_SPEC, dry_run=False)
    csv_path = brands_root / "acme-dogs" / "data" / "config" / "instagram_accounts.csv"
    hand_curated = "hashtag,tier,scan_frequency,category,notes\n#handcurated,1,daily,food,not derivable from any keyword\n"
    csv_path.write_text(hand_curated, encoding="utf-8")

    provision_brand(FULL_SPEC, dry_run=False)

    assert csv_path.read_text(encoding="utf-8") == hand_curated


@requires_postgres
def test_real_run_seeds_instagram_accounts_csv_from_keywords_when_new(
    pg: None, brands_root: Path
) -> None:
    """First-time provisioning still seeds the CSV from spec keywords --
    only re-provisioning an already-existing file is a no-op."""
    result = provision_brand(FULL_SPEC, dry_run=False)

    content = (result.brand_dir / "data" / "config" / "instagram_accounts.csv").read_text(
        encoding="utf-8"
    )
    assert "#dogfood" in content
    assert "#gps" in content


@requires_postgres
def test_real_run_merges_brand_json_preserving_hand_added_keys(pg: None, brands_root: Path) -> None:
    """brand.json is a shallow merge, not an overwrite: render_brand_json()
    only ever computes runtime/group_discovery, but an operator may have
    hand-added other top-level keys (rate_limits overrides, campaign config)
    render_brand_json() never owns. A full overwrite here silently dropped
    all of that on every settings edit -- same bug class as the CSV fix."""
    result = provision_brand(FULL_SPEC, dry_run=False)
    brand_json_path = result.brand_dir / "brand.json"
    data = json.loads(brand_json_path.read_text(encoding="utf-8"))
    data["profiles"] = {"facebook": {"rate_limits": {"comments_per_day": 8}}}
    brand_json_path.write_text(json.dumps(data), encoding="utf-8")

    provision_brand(FULL_SPEC, dry_run=False)

    data = json.loads(brand_json_path.read_text(encoding="utf-8"))
    assert data["profiles"]["facebook"]["rate_limits"]["comments_per_day"] == 8


@requires_postgres
def test_real_run_still_updates_brand_json_settings_fields(pg: None, brands_root: Path) -> None:
    """The merge must not freeze runtime/group_discovery at their first-ever
    value -- a settings-page edit (headless toggle, join limit) still has to
    take effect on every re-provision."""
    provision_brand(FULL_SPEC, dry_run=False)

    changed_spec = BrandSpec(**{**FULL_SPEC.__dict__, "headless": False, "group_join_limit": 3})
    result = provision_brand(changed_spec, dry_run=False)

    data = json.loads((result.brand_dir / "brand.json").read_text(encoding="utf-8"))
    assert data["runtime"]["headless"] is False
    assert data["group_discovery"]["join_limit_per_day"] == 3


@requires_postgres
def test_real_run_does_not_error_when_brand_folder_already_exists(
    pg: None, brands_root: Path
) -> None:
    (brands_root / "acme-dogs" / "data" / "config").mkdir(parents=True)
    (brands_root / "acme-dogs" / "config.json").write_text("{}", encoding="utf-8")

    result = provision_brand(FULL_SPEC, dry_run=False)  # must not raise
    data = json.loads((result.brand_dir / "config.json").read_text(encoding="utf-8"))
    assert data["site"]["name"] == "Acme Dogs"  # rewritten, not left as "{}"


@requires_postgres
def test_real_run_raises_on_unusable_slug(pg: None, brands_root: Path) -> None:
    spec = BrandSpec(name="!!!", site_url="https://x.example", niche="n")
    with pytest.raises(ValueError):
        provision_brand(spec, dry_run=False)
