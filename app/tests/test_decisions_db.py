# pyright: reportMissingImports=false
"""Integration tests for `lib.decisions.decisions_db` against the test DB.

Runs against the suite-owned `<name>_test` database conftest derives; skips
cleanly when no Postgres is reachable.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
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
    assert decisions_db.lookup_decision("b1", "instagram", "u1") is True  # stored would_skip
    assert decisions_db.lookup_decision("b1", "facebook", "u1") is None


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
    # Fill-once: an existing outcome is never overwritten (additive only).
    assert decisions_db.record_outcome("u1", "engaged", brand_id="b1") is False
    assert decisions_db.record_outcome("u1", "bogus") is False

    # A failed Jev call: a row, but never compared even with an outcome.
    decisions_db.record_decision(
        replace(_record("u5", would_skip=False), answers={}, cost_usd=None, error="jev_call_failed")
    )
    decisions_db.record_outcome("u5", "engaged", brand_id="b1")

    summary = decisions_db.summarize(brand_id="b1")
    assert (summary.total, summary.would_skip, summary.compared, summary.agreed) == (5, 2, 3, 2)
    assert summary.failed == 1
    assert summary.agreement_rate == pytest.approx(2 / 3)
    assert summary.total_cost_usd == pytest.approx(0.004)

    ig = decisions_db.summarize(brand_id="b1", platform="instagram")
    assert (ig.total, ig.compared, ig.agreed) == (4, 2, 1)

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


@requires_postgres
def test_pending_outcome_rides_the_insert(pg: None) -> None:
    decisions_db.record_decision(replace(_record("early", would_skip=True), outcome="declined"))
    row = decisions_db.list_recent(brand_id="b1")[0]
    assert row["outcome"] == "declined" and row["outcome_at"] is not None
    assert decisions_db.summarize(brand_id="b1").agreed == 1


@requires_postgres
def test_outcome_check_constraint(pg: None) -> None:
    decisions_db.record_decision(_record("u1", would_skip=False))
    with pytest.raises(Exception, match="outcome"):
        db.execute("UPDATE jev_decisions SET outcome = 'bogus' WHERE item_key = 'u1'")


def test_gate_path_never_raises_when_db_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("db down")

    monkeypatch.setattr(decisions_db, "get_pool", _boom)
    assert decisions_db.record_decision(_record("u", would_skip=False)) is False
    assert decisions_db.record_outcome("u", "engaged") is False
    # "Decided, keep": no second Jev call, and never a skip.
    assert decisions_db.lookup_decision("b1", "instagram", "u") is False


def test_gate_path_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Short pool checkout + SET LOCAL statement_timeout on every gate call."""
    seen: dict[str, object] = {}

    class _Cur:
        rowcount = 1

        def __enter__(self) -> _Cur:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def execute(self, sql: str, _params: object = None) -> None:
            seen.setdefault("first_sql", sql)

        def fetchone(self) -> None:
            return None

    class _Conn(_Cur):
        def cursor(self, **_k: object) -> _Cur:
            return _Cur()

    class _Pool:
        def connection(self, timeout: float) -> _Conn:
            seen["timeout"] = timeout
            return _Conn()

    monkeypatch.setattr(decisions_db, "get_pool", lambda: _Pool())
    assert decisions_db.lookup_decision("b1", "instagram", "u") is None
    assert seen["timeout"] == decisions_db.GATE_DB_TIMEOUT_S
    assert "statement_timeout" in str(seen["first_sql"])
