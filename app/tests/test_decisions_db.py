# pyright: reportMissingImports=false
"""Integration tests for `lib.decisions.decisions_db` against the test DB.

Runs against the suite-owned `<name>_test` database conftest derives; skips
cleanly when no Postgres is reachable.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from lib import db
from lib.decisions import decisions_db
from lib.decisions.decisions_db import DecisionRecord
from tests._pg import requires_postgres

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


@pytest.fixture
def pg() -> Iterator[None]:
    db.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        yield
    finally:
        db.execute("TRUNCATE TABLE jev_decisions")


def _record(
    key: str, *, would_skip: bool, brand: str = "b1", platform: str = "instagram"
) -> DecisionRecord:
    return DecisionRecord(
        brand_id=brand,
        flow="ig-engager",
        platform=platform,
        item_key=key,
        questions={"relevant": {"type": "noul"}},
        answers={"relevant": {"noul": 0.1}},
        mode="shadow",
        would_skip=would_skip,
        latency_ms=40,
        cost_usd=0.001,
    )


@requires_postgres
def test_record_is_idempotent_per_item(pg: None) -> None:
    assert decisions_db.record_decision(_record("u1", would_skip=True)) is True
    assert decisions_db.record_decision(_record("u1", would_skip=False)) is False
    rows = decisions_db.list_recent(brand_id="b1")
    assert len(rows) == 1
    assert rows[0]["would_skip"] is True  # the first decision is kept
    assert rows[0]["answers"] == {"relevant": {"noul": 0.1}}
    assert decisions_db.has_decision("b1", "instagram", "u1") is True
    assert decisions_db.has_decision("b1", "facebook", "u1") is False


@requires_postgres
def test_record_outcome_and_summary(pg: None) -> None:
    decisions_db.record_decision(_record("u1", would_skip=True))
    decisions_db.record_decision(_record("u2", would_skip=False))
    decisions_db.record_decision(_record("u3", would_skip=True))
    decisions_db.record_decision(_record("u4", would_skip=False, platform="facebook"))

    assert decisions_db.record_outcome("u1", "declined", brand_id="b1") is True  # agree
    assert decisions_db.record_outcome("u2", "declined", brand_id="b1") is True  # disagree
    assert decisions_db.record_outcome("u3", "drafter_error") is True  # not compared
    assert decisions_db.record_outcome("u4", "engaged", platform="facebook") is True  # agree
    assert decisions_db.record_outcome("nope", "engaged") is False
    assert decisions_db.record_outcome("u1", "bogus") is False

    summary = decisions_db.summarize(brand_id="b1")
    assert (summary.total, summary.would_skip, summary.compared, summary.agreed) == (4, 2, 3, 2)
    assert summary.agreement_rate == pytest.approx(2 / 3)
    assert summary.total_cost_usd == pytest.approx(0.004)

    ig = decisions_db.summarize(brand_id="b1", platform="instagram")
    assert (ig.total, ig.compared, ig.agreed) == (3, 2, 1)

    row = next(r for r in decisions_db.list_recent(brand_id="b1") if r["item_key"] == "u1")
    assert row["outcome"] == "declined" and row["outcome_at"] is not None


@requires_postgres
def test_summary_empty_and_list_filters(pg: None) -> None:
    empty = decisions_db.summarize(brand_id="nobody")
    assert (empty.total, empty.agreement_rate, empty.total_cost_usd) == (0, None, 0.0)

    for i in range(5):
        decisions_db.record_decision(_record(f"k{i}", would_skip=False))
    decisions_db.record_decision(_record("other", would_skip=False, brand="b2"))
    assert len(decisions_db.list_recent(brand_id="b1", limit=3)) == 3
    assert [r["item_key"] for r in decisions_db.list_recent(brand_id="b2")] == ["other"]
    assert len(decisions_db.list_recent()) == 6


@requires_postgres
def test_mode_check_constraint(pg: None) -> None:
    rec = DecisionRecord(
        brand_id="b1",
        flow="f",
        platform="instagram",
        item_key="x",
        questions={},
        answers={},
        mode="loud",
        would_skip=False,  # type: ignore[arg-type]
    )
    # The write swallows the CHECK violation (never raises) and reports False.
    assert decisions_db.record_decision(rec) is False


def test_writes_never_raise_when_db_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> int:
        raise RuntimeError("db down")

    monkeypatch.setattr(decisions_db.db, "execute", _boom)
    monkeypatch.setattr(decisions_db.db, "fetch_one", _boom)
    assert decisions_db.record_decision(_record("u", would_skip=False)) is False
    assert decisions_db.record_outcome("u", "engaged") is False
    assert decisions_db.has_decision("b1", "instagram", "u") is True
