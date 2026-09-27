"""Repository for ``jev_decisions`` (schema in ``db/schema.sql``).

Writes are called from inside a live engager run, so they NEVER raise: a DB
hiccup is logged and reported as ``False``. Reads back the API and may
raise like any other query.

Idempotent by construction: ``(brand_id, platform, item_key)`` is unique and
``record_decision`` is ``ON CONFLICT DO NOTHING`` -- a re-visited post keeps
its first decision (the engager asks ``has_decision`` first so it does not
pay Jev twice), and ``record_outcome`` only ever fills the outcome columns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from psycopg.types.json import Jsonb

from lib import db
from lib.decisions.jev_types import JsonDict
from lib.decisions.modes import GateMode
from lib.observability import get_logger

log = get_logger(__name__)

OUTCOME_ENGAGED: Final = "engaged"
OUTCOME_DECLINED: Final = "declined"
OUTCOME_DRAFTER_ERROR: Final = "drafter_error"
OUTCOME_SKIPPED_BY_GATE: Final = "skipped_by_gate"
OUTCOMES: Final = frozenset(
    {OUTCOME_ENGAGED, OUTCOME_DECLINED, OUTCOME_DRAFTER_ERROR, OUTCOME_SKIPPED_BY_GATE}
)

MAX_LIST_LIMIT: Final = 500


@dataclass(frozen=True)
class DecisionRecord:
    """Everything one evaluated post contributes to the log."""

    brand_id: str
    flow: str
    platform: str
    item_key: str
    questions: JsonDict
    answers: JsonDict
    mode: GateMode
    would_skip: bool
    latency_ms: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True)
class DecisionSummary:
    """Totals over every matching row (not just the listed page).

    ``agreement_rate`` compares Jev's ``would_skip`` with what the drafter
    did: agree = (would_skip and declined) or (not would_skip and engaged).
    Rows without an engaged/declined outcome are not counted; ``None`` when
    there is nothing to compare yet.
    """

    total: int
    would_skip: int
    compared: int
    agreed: int
    agreement_rate: float | None
    total_cost_usd: float


def record_decision(record: DecisionRecord) -> bool:
    """Insert one decision; True if a row was written. Never raises."""
    try:
        written = db.execute(
            """
            INSERT INTO jev_decisions
                (brand_id, flow, platform, item_key, questions, answers,
                 mode, would_skip, latency_ms, cost_usd)
            VALUES
                (%(brand_id)s, %(flow)s, %(platform)s, %(item_key)s, %(questions)s,
                 %(answers)s, %(mode)s, %(would_skip)s, %(latency_ms)s, %(cost_usd)s)
            ON CONFLICT (brand_id, platform, item_key) DO NOTHING
            """,
            {
                "brand_id": record.brand_id,
                "flow": record.flow,
                "platform": record.platform,
                "item_key": record.item_key,
                "questions": Jsonb(record.questions),
                "answers": Jsonb(record.answers),
                "mode": record.mode,
                "would_skip": record.would_skip,
                "latency_ms": record.latency_ms,
                "cost_usd": record.cost_usd,
            },
        )
    except Exception as exc:
        log.warning("jev_decision_record_failed", error_type=type(exc).__name__)
        return False
    return written > 0


def has_decision(brand_id: str, platform: str, item_key: str) -> bool:
    """True when this post already has a logged decision. Never raises
    (a failed lookup answers True, so an outage never buys a second Jev call)."""
    try:
        row = db.fetch_one(
            "SELECT 1 AS hit FROM jev_decisions "
            "WHERE brand_id = %s AND platform = %s AND item_key = %s",
            (brand_id, platform, item_key),
        )
    except Exception as exc:
        log.warning("jev_decision_lookup_failed", error_type=type(exc).__name__)
        return True
    return row is not None


def record_outcome(
    item_key: str,
    outcome: str,
    *,
    brand_id: str | None = None,
    platform: str | None = None,
) -> bool:
    """Stamp what the drafter did for ``item_key``. Never raises.

    ``brand_id``/``platform`` narrow the match when given (the engager always
    passes both). A later visit overwrites the outcome: it reflects the most
    recent drafter decision for that post.
    """
    if outcome not in OUTCOMES:
        log.warning("jev_outcome_invalid", outcome=outcome)
        return False
    clauses = ["item_key = %(item_key)s"]
    params: dict[str, object] = {"item_key": item_key, "outcome": outcome}
    if brand_id is not None:
        clauses.append("brand_id = %(brand_id)s")
        params["brand_id"] = brand_id
    if platform is not None:
        clauses.append("platform = %(platform)s")
        params["platform"] = platform
    try:
        updated = db.execute(
            "UPDATE jev_decisions SET outcome = %(outcome)s, outcome_at = NOW() "
            f"WHERE {' AND '.join(clauses)}",
            params,
        )
    except Exception as exc:
        log.warning("jev_outcome_record_failed", error_type=type(exc).__name__)
        return False
    return updated > 0


def _filters(brand_id: str | None, platform: str | None) -> tuple[str, dict[str, object]]:
    clauses: list[str] = []
    params: dict[str, object] = {}
    if brand_id:
        clauses.append("brand_id = %(brand_id)s")
        params["brand_id"] = brand_id
    if platform:
        clauses.append("platform = %(platform)s")
        params["platform"] = platform
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def list_recent(
    *, brand_id: str | None = None, platform: str | None = None, limit: int = 50
) -> list[dict[str, object]]:
    """Newest-first decision rows, optionally filtered."""
    where, params = _filters(brand_id, platform)
    params["limit"] = max(1, min(int(limit), MAX_LIST_LIMIT))
    return db.fetch_all(
        f"SELECT * FROM jev_decisions {where} ORDER BY created_at DESC, id DESC LIMIT %(limit)s",
        params,
    )


def summarize(*, brand_id: str | None = None, platform: str | None = None) -> DecisionSummary:
    """Totals, would-skip count, drafter agreement and spend for the filter."""
    where, params = _filters(brand_id, platform)
    row = (
        db.fetch_one(
            f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE would_skip) AS would_skip,
            COUNT(*) FILTER (WHERE outcome IN ('engaged', 'declined')) AS compared,
            COUNT(*) FILTER (
                WHERE (would_skip AND outcome = 'declined')
                   OR (NOT would_skip AND outcome = 'engaged')
            ) AS agreed,
            COALESCE(SUM(cost_usd), 0) AS total_cost_usd
        FROM jev_decisions {where}
        """,
            params,
        )
        or {}
    )
    compared = int(row.get("compared") or 0)
    agreed = int(row.get("agreed") or 0)
    return DecisionSummary(
        total=int(row.get("total") or 0),
        would_skip=int(row.get("would_skip") or 0),
        compared=compared,
        agreed=agreed,
        agreement_rate=(agreed / compared) if compared else None,
        total_cost_usd=float(row.get("total_cost_usd") or 0.0),
    )
