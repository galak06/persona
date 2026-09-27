"""Repository for ``jev_decisions`` (schema in ``db/schema.sql``).

The gate-path calls (``record_decision``, ``lookup_decision``,
``record_outcome``) run inside a live engager, so they NEVER raise and are
BOUNDED: each takes a pooled connection with a short checkout timeout and
runs under ``SET LOCAL statement_timeout``, so a slow database costs the
gate a couple of seconds at most, never the pool's 30s default. The read
side (``list_recent``, ``summarize``) backs the API and may raise normally.

Idempotent by construction: ``(brand_id, platform, item_key)`` is unique and
``record_decision`` is ``ON CONFLICT DO NOTHING`` -- a re-visited post keeps
its first decision (the gate reads it back via ``lookup_decision`` instead
of paying Jev twice), and ``record_outcome`` only ever fills a NULL
outcome -- nothing already recorded is rewritten.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final

from psycopg import Cursor
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb

from lib import db
from lib.db_pool import get_pool
from lib.decisions.jev_types import JsonDict
from lib.decisions.modes import GateMode

# The outcome vocabulary lives in lib.decisions.outcomes (it grew the
# fb-group-scout outcomes); slice-1 callers read these names off this module.
from lib.decisions.outcomes import KEEP_OUTCOMES, SKIP_OUTCOMES
from lib.decisions.outcomes import OUTCOME_DECLINED as OUTCOME_DECLINED
from lib.decisions.outcomes import OUTCOME_DRAFTER_ERROR as OUTCOME_DRAFTER_ERROR
from lib.decisions.outcomes import OUTCOME_ENGAGED as OUTCOME_ENGAGED
from lib.decisions.outcomes import OUTCOME_SKIPPED_BY_GATE as OUTCOME_SKIPPED_BY_GATE
from lib.decisions.outcomes import OUTCOMES as OUTCOMES
from lib.observability import get_logger

log = get_logger(__name__)

ERROR_JEV_CALL_FAILED: Final = "jev_call_failed"

MAX_LIST_LIMIT: Final = 500
# Gate-path bounds: pool checkout wait and per-statement server timeout.
GATE_DB_TIMEOUT_S: Final = 2.0
_STATEMENT_TIMEOUT: Final = "2s"


@dataclass(frozen=True)
class DecisionRecord:
    """Everything one evaluated post contributes to the log.

    ``error`` is set (and ``answers`` empty) when the Jev call failed;
    ``outcome`` carries a drafter outcome that arrived before the row did.
    """

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
    outcome: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class DecisionSummary:
    """Totals over every matching row (not just the listed page).

    ``agreement_rate`` compares Jev's ``would_skip`` with what the flow did:
    agree = (would_skip and a ``SKIP_OUTCOMES`` outcome) or (not would_skip
    and a ``KEEP_OUTCOMES`` outcome) -- declined/engaged for the engagers,
    the scout's editorial skips/joins for ``fb_group``. Failed calls and
    other outcomes are not compared; ``None`` when there is nothing to
    compare yet.
    """

    total: int
    would_skip: int
    failed: int
    compared: int
    agreed: int
    agreement_rate: float | None
    total_cost_usd: float


@contextmanager
def _gate_cursor() -> Iterator[Cursor[DictRow]]:
    """A bounded cursor for gate-path statements (see module docstring)."""
    with (
        get_pool().connection(timeout=GATE_DB_TIMEOUT_S) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'")
        yield cur


def record_decision(record: DecisionRecord) -> bool:
    """Insert one decision; True if a row was written. Never raises."""
    outcome = record.outcome if record.outcome in OUTCOMES else None
    try:
        with _gate_cursor() as cur:
            cur.execute(
                """
                INSERT INTO jev_decisions
                    (brand_id, flow, platform, item_key, questions, answers, mode,
                     would_skip, latency_ms, cost_usd, outcome, outcome_at, error)
                VALUES
                    (%(brand_id)s, %(flow)s, %(platform)s, %(item_key)s, %(questions)s,
                     %(answers)s, %(mode)s, %(would_skip)s, %(latency_ms)s, %(cost_usd)s,
                     %(outcome)s, CASE WHEN %(outcome)s::text IS NULL THEN NULL ELSE NOW() END,
                     %(error)s)
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
                    "outcome": outcome,
                    "error": record.error,
                },
            )
            return cur.rowcount > 0
    except Exception as exc:
        log.warning("jev_decision_record_failed", error_type=type(exc).__name__)
        return False


def lookup_decision(brand_id: str, platform: str, item_key: str) -> bool | None:
    """The stored ``would_skip`` for this post, or None if it has no row.

    Never raises. A failed lookup answers ``False`` ("decided, keep"): an
    outage must neither buy a second Jev call nor skip a post.
    """
    try:
        with _gate_cursor() as cur:
            cur.execute(
                "SELECT would_skip FROM jev_decisions "
                "WHERE brand_id = %s AND platform = %s AND item_key = %s",
                (brand_id, platform, item_key),
            )
            row = cur.fetchone()
    except Exception as exc:
        log.warning("jev_decision_lookup_failed", error_type=type(exc).__name__)
        return False
    return None if row is None else bool(row["would_skip"])


def record_outcome(
    item_key: str,
    outcome: str,
    *,
    brand_id: str | None = None,
    platform: str | None = None,
) -> bool:
    """Stamp what the flow (drafter or scout) did for ``item_key``. Never raises.

    ``brand_id``/``platform`` narrow the match when given (the engager always
    passes both). Fill-once: only a row whose outcome is still NULL is
    updated, so an existing outcome (another run's agreement evidence) is
    never overwritten. Returns False when nothing was filled.
    """
    if outcome not in OUTCOMES:
        log.warning("jev_outcome_invalid", outcome=outcome)
        return False
    clauses = ["item_key = %(item_key)s", "outcome IS NULL"]
    params: dict[str, object] = {"item_key": item_key, "outcome": outcome}
    if brand_id is not None:
        clauses.append("brand_id = %(brand_id)s")
        params["brand_id"] = brand_id
    if platform is not None:
        clauses.append("platform = %(platform)s")
        params["platform"] = platform
    try:
        with _gate_cursor() as cur:
            cur.execute(
                "UPDATE jev_decisions SET outcome = %(outcome)s, outcome_at = NOW() "
                f"WHERE {' AND '.join(clauses)}",
                params,
            )
            return cur.rowcount > 0
    except Exception as exc:
        log.warning("jev_outcome_record_failed", error_type=type(exc).__name__)
        return False


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
    """Totals, would-skip count, failures, drafter agreement and spend."""
    where, params = _filters(brand_id, platform)
    params.update(keep=list(KEEP_OUTCOMES), skip=list(SKIP_OUTCOMES))
    row = (
        db.fetch_one(
            f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE would_skip) AS would_skip,
                COUNT(*) FILTER (WHERE error IS NOT NULL) AS failed,
                COUNT(*) FILTER (
                    WHERE error IS NULL
                      AND (outcome = ANY(%(keep)s) OR outcome = ANY(%(skip)s))
                ) AS compared,
                COUNT(*) FILTER (
                    WHERE error IS NULL
                      AND ((would_skip AND outcome = ANY(%(skip)s))
                        OR (NOT would_skip AND outcome = ANY(%(keep)s)))
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
        failed=int(row.get("failed") or 0),
        compared=compared,
        agreed=agreed,
        agreement_rate=(agreed / compared) if compared else None,
        total_cost_usd=float(row.get("total_cost_usd") or 0.0),
    )
