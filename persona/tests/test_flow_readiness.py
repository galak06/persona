"""Tests for `lib/flow_readiness.py` (per-flow readiness + last-run status).

`_hashtag_count`/`_readiness_for("ig-scanner", ...)` are pure filesystem
reads -- no Postgres needed. Everything touching joined-group counts or
`worker_runs` is a real integration test against a live local Postgres,
following the project's `requires_postgres` skipif convention.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from lib import db, worker_db
from lib.brands_db.repository import BrandsRepository
from lib.flow_readiness import _hashtag_count, _joined_group_count, _readiness_for, flow_status

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
_BRAND = "flow-readiness-brand"


def _postgres_reachable() -> bool:
    try:
        return db.health_check()
    except Exception:
        return False


_PG_AVAILABLE = _postgres_reachable()
requires_postgres = pytest.mark.skipif(
    not _PG_AVAILABLE, reason="No reachable Postgres at DATABASE_URL"
)


@pytest.fixture
def pg() -> Iterator[None]:
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE fb_groups, worker_runs, schedule_tasks, brands CASCADE")


@pytest.fixture
def brand(pg: None) -> str:
    BrandsRepository().create(
        brand_id=_BRAND, name="Flow Readiness Brand", site_url="https://x.example", niche="n"
    )
    return _BRAND


def _insert_group(brand_id: str, group_url: str, status: str) -> None:
    db.execute(
        "INSERT INTO fb_groups (id, brand_id, group_url, group_name, status) "
        "VALUES (%s, %s, %s, %s, %s)",
        (group_url, brand_id, group_url, group_url, status),
    )


# --------------------------------------------------------------------------- _hashtag_count (pure)


def test_hashtag_count_missing_file_returns_zero(tmp_path: Path) -> None:
    assert _hashtag_count(tmp_path) == 0


def test_hashtag_count_counts_data_rows_not_header(tmp_path: Path) -> None:
    csv_dir = tmp_path / "data" / "config"
    csv_dir.mkdir(parents=True)
    (csv_dir / "instagram_accounts.csv").write_text(
        "hashtag,tier,scan_frequency,category,notes\n#dogfood,1,daily,general,\n#gps,2,every_2_days,general,\n",
        encoding="utf-8",
    )
    assert _hashtag_count(tmp_path) == 2


# --------------------------------------------------------------------------- _readiness_for (ig-scanner: pure)


def test_readiness_for_ig_scanner_not_ready_when_no_hashtags(tmp_path: Path) -> None:
    readiness = _readiness_for("ig-scanner", brand_id="x", brand_dir=tmp_path)
    assert readiness["signal"] == "hashtags"
    assert readiness["count"] == 0
    assert readiness["ready"] is False
    assert "No hashtags" in readiness["hint"]


def test_readiness_for_ig_scanner_ready_when_hashtags_present(tmp_path: Path) -> None:
    csv_dir = tmp_path / "data" / "config"
    csv_dir.mkdir(parents=True)
    (csv_dir / "instagram_accounts.csv").write_text(
        "hashtag,tier,scan_frequency,category,notes\n#dogfood,1,daily,general,\n",
        encoding="utf-8",
    )
    readiness = _readiness_for("ig-scanner", brand_id="x", brand_dir=tmp_path)
    assert readiness["ready"] is True
    assert readiness["count"] == 1


# --------------------------------------------------------------------------- joined-group readiness (Postgres)


@requires_postgres
def test_joined_group_count_counts_only_joined_status(brand: str, tmp_path: Path) -> None:
    _insert_group(brand, "https://facebook.com/groups/1", "joined")
    _insert_group(brand, "https://facebook.com/groups/2", "joined")
    _insert_group(brand, "https://facebook.com/groups/3", "join_requested")

    assert _joined_group_count(brand) == 2


@requires_postgres
def test_readiness_for_fb_group_scout_not_ready_with_zero_joined(
    brand: str, tmp_path: Path
) -> None:
    readiness = _readiness_for("fb-group-scout", brand_id=brand, brand_dir=tmp_path)
    assert readiness["signal"] == "joined_groups"
    assert readiness["ready"] is False
    assert "approve" in readiness["hint"].lower()


@requires_postgres
def test_readiness_for_fb_scanner_ready_once_a_group_is_joined(brand: str, tmp_path: Path) -> None:
    _insert_group(brand, "https://facebook.com/groups/1", "joined")
    readiness = _readiness_for("fb-scanner", brand_id=brand, brand_dir=tmp_path)
    assert readiness["ready"] is True
    assert readiness["count"] == 1


# --------------------------------------------------------------------------- flow_status


@requires_postgres
def test_flow_status_returns_entries_in_onboarding_order(brand: str, tmp_path: Path) -> None:
    entries = flow_status(brand_id=brand, brand_dir=tmp_path, enabled_flows=["ig-scanner"])
    assert [e["flow_id"] for e in entries] == ["ig-scanner", "fb-scanner", "fb-group-scout"]


@requires_postgres
def test_flow_status_reflects_enabled_flows(brand: str, tmp_path: Path) -> None:
    entries = flow_status(
        brand_id=brand, brand_dir=tmp_path, enabled_flows=["ig-scanner", "fb-scanner"]
    )
    by_id = {e["flow_id"]: e for e in entries}
    assert by_id["ig-scanner"]["enabled"] is True
    assert by_id["fb-scanner"]["enabled"] is True
    assert by_id["fb-group-scout"]["enabled"] is False


@requires_postgres
def test_flow_status_last_run_none_when_never_run(brand: str, tmp_path: Path) -> None:
    entries = flow_status(brand_id=brand, brand_dir=tmp_path, enabled_flows=["ig-scanner"])
    assert all(e["last_run"] is None for e in entries)


@requires_postgres
def test_flow_status_reflects_worker_runs(brand: str, tmp_path: Path) -> None:
    worker_db.record_complete(tmp_path, f"{brand}-ig-scanner", brand, "success", "found 3 posts")

    entries = flow_status(brand_id=brand, brand_dir=tmp_path, enabled_flows=["ig-scanner"])
    by_id = {e["flow_id"]: e for e in entries}
    assert by_id["ig-scanner"]["last_run"]["status"] == "success"
    assert by_id["ig-scanner"]["last_run"]["message"] == "found 3 posts"
    assert by_id["fb-scanner"]["last_run"] is None
