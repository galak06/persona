"""Data-sufficiency gate for the GSC signal (plan Slice 2).

Shared by `backfill_gsc_content.py` and `lib.gsc_scout` so the
>=15-queries/>=40-impressions-in-position-6-25 bar lives in one place.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

DEFAULT_MIN_POSITION = 6.0
DEFAULT_MAX_POSITION = 25.0
DEFAULT_MIN_IMPRESSIONS = 40
DEFAULT_MIN_QUERIES = 15


@dataclass(frozen=True)
class DataSufficiencyResult:
    sufficient: bool
    qualifying_query_count: int
    threshold: int


def evaluate_data_sufficiency(
    query_rows: Iterable[tuple[str, float, int]],
    *,
    min_position: float = DEFAULT_MIN_POSITION,
    max_position: float = DEFAULT_MAX_POSITION,
    min_impressions: int = DEFAULT_MIN_IMPRESSIONS,
    min_queries: int = DEFAULT_MIN_QUERIES,
) -> DataSufficiencyResult:
    """Evaluate the gate against `(query, position, impressions)` tuples.

    Deliberately shape-agnostic (plain tuples, not `GscRow` or a DB row
    dict) so both callers -- one working with freshly fetched API rows, the
    other with `published_content` table rows -- can feed it without either
    depending on the other's data type.
    """
    qualifying = {
        query
        for query, position, impressions in query_rows
        if min_position <= position <= max_position and impressions >= min_impressions
    }
    count = len(qualifying)
    return DataSufficiencyResult(
        sufficient=count >= min_queries,
        qualifying_query_count=count,
        threshold=min_queries,
    )
