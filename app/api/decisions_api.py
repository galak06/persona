"""Read-only API over the Jev decision log (`jev_decisions`).

`GET /decisions` returns the most recent decisions plus a summary computed
over EVERY matching row (not just the returned page): totals, how many
items Jev would have skipped, how often that agreed with what the flow
actually did (the drafter for posts, the group scout for ``fb_group``),
and the total spend. Strictly read-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from lib.decisions import decisions_db
from lib.decisions.outcomes import agrees

router = APIRouter()


class JevDecision(BaseModel):
    """One evaluated post."""

    id: int
    brand_id: str
    flow: str = ""
    platform: str
    item_key: str
    questions: dict[str, Any] = {}
    answers: dict[str, Any] = {}
    mode: str
    would_skip: bool
    latency_ms: int | None = None
    cost_usd: float | None = None
    created_at: str = ""
    outcome: str | None = None
    outcome_at: str | None = None
    # Set when the Jev call failed (answers empty); such rows never count
    # toward agreement.
    error: str | None = None
    # True/False once the flow made an editorial keep/skip call (drafter
    # engaged/declined, scout joined/skipped); None before that or for
    # outcomes that are not a judgement (budget cap, join errors, gate skip).
    agrees: bool | None = None


class DecisionsSummary(BaseModel):
    total: int
    would_skip: int
    failed: int = 0
    compared: int
    agreed: int
    agreement_rate: float | None = None
    total_cost_usd: float


class DecisionsResponse(BaseModel):
    decisions: list[JevDecision]
    summary: DecisionsSummary


def _iso(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _agrees(would_skip: bool, outcome: object, error: object) -> bool | None:
    if error is not None:
        return None
    return agrees(would_skip, outcome)


def _to_model(row: dict[str, Any]) -> JevDecision:
    would_skip = bool(row.get("would_skip"))
    outcome = row.get("outcome")
    cost = row.get("cost_usd")
    latency = row.get("latency_ms")
    return JevDecision(
        id=int(row["id"]),
        brand_id=str(row.get("brand_id") or ""),
        flow=str(row.get("flow") or ""),
        platform=str(row.get("platform") or ""),
        item_key=str(row.get("item_key") or ""),
        questions=dict(row.get("questions") or {}),
        answers=dict(row.get("answers") or {}),
        mode=str(row.get("mode") or ""),
        would_skip=would_skip,
        latency_ms=int(latency) if latency is not None else None,
        cost_usd=float(cost) if cost is not None else None,
        created_at=_iso(row.get("created_at")) or "",
        outcome=str(outcome) if outcome is not None else None,
        outcome_at=_iso(row.get("outcome_at")),
        error=str(row["error"]) if row.get("error") is not None else None,
        agrees=_agrees(would_skip, outcome, row.get("error")),
    )


@router.get("/decisions", response_model=DecisionsResponse)
def list_decisions(
    brand_id: str | None = Query(None, description="Brand id; omit for all brands"),
    platform: str | None = Query(None, description="instagram | facebook | fb_group"),
    limit: int = Query(50, ge=1, le=decisions_db.MAX_LIST_LIMIT),
) -> DecisionsResponse:
    """Most-recent-first Jev decisions, optionally filtered, plus a summary."""
    rows = decisions_db.list_recent(brand_id=brand_id, platform=platform, limit=limit)
    summary = decisions_db.summarize(brand_id=brand_id, platform=platform)
    return DecisionsResponse(
        decisions=[_to_model(dict(r)) for r in rows],
        summary=DecisionsSummary(
            total=summary.total,
            would_skip=summary.would_skip,
            failed=summary.failed,
            compared=summary.compared,
            agreed=summary.agreed,
            agreement_rate=summary.agreement_rate,
            total_cost_usd=summary.total_cost_usd,
        ),
    )
