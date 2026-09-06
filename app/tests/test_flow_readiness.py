"""Tests for `lib/flow_readiness.py` (per-flow readiness + last-run status).

`_hashtag_count`/`_readiness_for("ig-engager", ...)` are pure filesystem
reads -- no Postgres needed. Everything touching joined-group counts or
`worker_runs` is a real integration test against a live local Postgres,
following the project's `requires_postgres` skipif convention.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from lib import db, schedule_db, worker_db
from lib.brands_db.repository import BrandsRepository
from lib.flow_readiness import (
    _hashtag_count,
    _joined_group_count,
    _readiness_for,
    _task_ids_by_flow,
    flow_status,
)
from lib.worker_labels import worker_label_for_flow

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
_BRAND = "flow-readiness-brand"


from tests._pg import requires_postgres


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


# --------------------------------------------------------------------------- _readiness_for (ig-engager: pure)


def test_readiness_for_ig_engager_not_ready_when_no_hashtags(tmp_path: Path) -> None:
    readiness = _readiness_for("ig-engager", brand_id="x", brand_dir=tmp_path)
    assert readiness["signal"] == "hashtags"
    assert readiness["count"] == 0
    assert readiness["ready"] is False
    assert "No hashtags" in readiness["hint"]


def test_readiness_for_ig_engager_ready_when_hashtags_present(tmp_path: Path) -> None:
    csv_dir = tmp_path / "data" / "config"
    csv_dir.mkdir(parents=True)
    (csv_dir / "instagram_accounts.csv").write_text(
        "hashtag,tier,scan_frequency,category,notes\n#dogfood,1,daily,general,\n",
        encoding="utf-8",
    )
    readiness = _readiness_for("ig-engager", brand_id="x", brand_dir=tmp_path)
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
def test_readiness_for_fb_engager_ready_once_a_group_is_joined(brand: str, tmp_path: Path) -> None:
    _insert_group(brand, "https://facebook.com/groups/1", "joined")
    readiness = _readiness_for("fb-engager", brand_id=brand, brand_dir=tmp_path)
    assert readiness["ready"] is True
    assert readiness["count"] == 1


# --------------------------------------------------------------------------- flow_status


@requires_postgres
def test_flow_status_returns_entries_in_onboarding_order(brand: str, tmp_path: Path) -> None:
    entries = flow_status(brand_id=brand, brand_dir=tmp_path, enabled_flows=["ig-engager"])
    assert [e["flow_id"] for e in entries] == ["ig-engager", "fb-engager"]


@requires_postgres
def test_flow_status_omits_panel_hidden_flows(brand: str, tmp_path: Path) -> None:
    """`fb-group-scout` is gated and dispatched like any managed flow but
    renders no card -- the operator acts on its output in the Inbox, and its
    readiness signal already appears on the `fb-engager` card. Enabling it
    must not make a card appear."""
    entries = flow_status(
        brand_id=brand,
        brand_dir=tmp_path,
        enabled_flows=["ig-engager", "fb-group-scout", "fb-engager"],
    )
    assert "fb-group-scout" not in {e["flow_id"] for e in entries}


@requires_postgres
def test_flow_status_reflects_enabled_flows(brand: str, tmp_path: Path) -> None:
    entries = flow_status(brand_id=brand, brand_dir=tmp_path, enabled_flows=["ig-engager"])
    by_id = {e["flow_id"]: e for e in entries}
    assert by_id["ig-engager"]["enabled"] is True
    assert by_id["fb-engager"]["enabled"] is False


@requires_postgres
def test_flow_status_last_run_none_when_never_run(brand: str, tmp_path: Path) -> None:
    entries = flow_status(brand_id=brand, brand_dir=tmp_path, enabled_flows=["ig-engager"])
    assert all(e["last_run"] is None for e in entries)


@requires_postgres
def test_flow_status_reflects_worker_runs(brand: str, tmp_path: Path) -> None:
    task_id = worker_label_for_flow("ig-engager")
    schedule_db.save_task(
        None,
        {"id": task_id, "brand_id": brand, "title": "ig-engager", "script": "x", "schedule": {}},
    )
    worker_db.record_complete(tmp_path, task_id, brand, "success", "found 3 posts")

    entries = flow_status(brand_id=brand, brand_dir=tmp_path, enabled_flows=["ig-engager"])
    by_id = {e["flow_id"]: e for e in entries}
    assert by_id["ig-engager"]["last_run"]["status"] == "success"
    assert by_id["ig-engager"]["last_run"]["message"] == "found 3 posts"
    assert by_id["fb-engager"]["last_run"] is None


# --------------------------------------------------------------------------- _task_ids_by_flow (pure)
#
# `schedule_tasks.title` is NOT unique per brand: the 2026-08-31 task-id
# migration retired each superseded row IN PLACE (cron -> `cron_disabled`,
# `disabled_reason` recorded) rather than deleting it. These exercise the
# title -> id resolution directly, with `schedule_db.load_all` faked -- no DB.


def _row(task_id: str, title: str, *, brand_id: str = _BRAND, **schedule: str) -> dict[str, object]:
    return {"id": task_id, "title": title, "brand_id": brand_id, "schedule": dict(schedule)}


def _fake_rows(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, object]]) -> None:
    monkeypatch.setattr(schedule_db, "load_all", lambda conn=None: rows)


def test_task_ids_prefer_the_live_row_over_its_retired_twin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported bug. `load_all` orders by `order_num ASC, id ASC`, so the
    retired legacy row (order_num 30) came LAST and won a plain dict
    comprehension -- the panel then read its `worker_runs` row, frozen at the
    migration's hand-written error, while the live row was `success`."""
    _fake_rows(
        monkeypatch,
        [
            _row(f"{_BRAND}-ig-engager", "ig-engager", cron="0 19 * * *"),
            _row(
                "dogfood-ig-engager",
                "ig-engager",
                cron_disabled="0 19 * * *",
                disabled_reason="superseded by the brand-derived task id",
            ),
        ],
    )
    assert _task_ids_by_flow(_BRAND) == {"ig-engager": f"{_BRAND}-ig-engager"}


def test_task_ids_skip_a_row_retired_by_reason_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """fb-scanner/fb-comment were retired before there was a cron worth
    preserving -- `disabled_reason` alone marks those, exactly as the
    dispatcher's `schedule_db.is_retired` reads them."""
    _fake_rows(
        monkeypatch,
        [
            _row("dogfood-fb-engager", "fb-engager", disabled_reason="superseded"),
            _row(f"{_BRAND}-fb-engager", "fb-engager", cron="33 15 * * *"),
        ],
    )
    assert _task_ids_by_flow(_BRAND) == {"fb-engager": f"{_BRAND}-fb-engager"}


def test_task_ids_resolve_a_flow_that_has_only_a_live_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No regression for the ordinary case: one live row, and legacy-prefixed
    ids still resolve when they are the flow's only surviving row."""
    _fake_rows(
        monkeypatch,
        [
            _row(f"{_BRAND}-ig-engager", "ig-engager", cron="0 19 * * *"),
            _row("dogfood-fb-group-scout", "fb-group-scout", cron="0 9 * * *"),
            _row("other-brand-ig-engager", "ig-engager", brand_id="other", cron="0 19 * * *"),
        ],
    )
    assert _task_ids_by_flow(_BRAND) == {
        "ig-engager": f"{_BRAND}-ig-engager",
        "fb-group-scout": "dogfood-fb-group-scout",
    }


def test_task_ids_collision_prefers_the_canonical_brand_derived_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two LIVE rows sharing a title must not resolve by row order. The
    canonical `<brand_id>-<flow_id>` wins whichever side it arrives on."""
    canonical = f"{_BRAND}-ig-engager"
    rows = [
        _row("aaa-legacy-ig-engager", "ig-engager", cron="0 19 * * *"),
        _row(canonical, "ig-engager", cron="0 19 * * *"),
    ]
    _fake_rows(monkeypatch, rows)
    assert _task_ids_by_flow(_BRAND) == {"ig-engager": canonical}

    _fake_rows(monkeypatch, list(reversed(rows)))
    assert _task_ids_by_flow(_BRAND) == {"ig-engager": canonical}


def test_task_ids_collision_without_a_canonical_row_is_order_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither id is canonical -- resolution still must not depend on which
    row `load_all` happened to yield last."""
    rows = [
        _row("zzz-ig-engager", "ig-engager", cron="0 19 * * *"),
        _row("aaa-ig-engager", "ig-engager", cron="0 19 * * *"),
    ]
    _fake_rows(monkeypatch, rows)
    forward = _task_ids_by_flow(_BRAND)
    _fake_rows(monkeypatch, list(reversed(rows)))
    assert forward == _task_ids_by_flow(_BRAND) == {"ig-engager": "aaa-ig-engager"}


@requires_postgres
def test_flow_status_reads_the_live_row_not_the_retired_twin(brand: str, tmp_path: Path) -> None:
    """End to end through `flow_status`: the retired twin sorts last (higher
    `order_num`) and its `worker_runs` row is frozen at the migration error.
    The card must report the LIVE row's run."""
    live_id = worker_label_for_flow("ig-engager", brand)
    schedule_db.save_task(
        None,
        {
            "id": live_id,
            "brand_id": brand,
            "title": "ig-engager",
            "order_num": 0,
            "script": "x",
            "schedule": {"cron": "0 19 * * *"},
        },
    )
    schedule_db.save_task(
        None,
        {
            "id": "dogfood-ig-engager",
            "brand_id": brand,
            "title": "ig-engager",
            "order_num": 30,
            "script": "x",
            "schedule": {"cron_disabled": "0 19 * * *", "disabled_reason": "superseded"},
        },
    )
    worker_db.record_complete(tmp_path, "dogfood-ig-engager", brand, "error", "migration note")
    worker_db.record_complete(tmp_path, live_id, brand, "success", "liked 12 posts")

    by_id = {
        e["flow_id"]: e
        for e in flow_status(brand_id=brand, brand_dir=tmp_path, enabled_flows=["ig-engager"])
    }
    assert by_id["ig-engager"]["last_run"]["status"] == "success"
    assert by_id["ig-engager"]["last_run"]["message"] == "liked 12 posts"
