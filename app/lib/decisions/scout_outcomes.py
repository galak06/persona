"""One final outcome per group per run, and the scout's flow deadline.

The same group can meet several fates in one scout run: a pending-queue copy
and a fresh search copy of it share one ``item_key``, so the search copy can
be recorded ``skipped_low_score`` while the pending copy is joined.
``FinalOutcomes`` keeps the one that reflects what the scout finally DID:
a higher-priority outcome is never downgraded, and the gate stamps each key
exactly once, at ``close()`` (``record_outcome`` is fill-once, so an early
stamp could never be corrected).

Priority, highest first:

1. ``skipped_by_gate`` -- enforce removed the group; nothing else happened.
2. a join attempt's result (joined, join_requested, already_member,
   already_pending, join_failed) -- the scout's real final action.
3. ``skipped_cap`` -- the group was a viable candidate but out of budget.
4. the editorial skips (low score, admission closed, rank cut).
"""

from __future__ import annotations

import os
import threading
from typing import Final

from lib.decisions import outcomes

_PRIORITY: Final[dict[str, int]] = {
    outcomes.OUTCOME_SKIPPED_BY_GATE: 4,
    outcomes.OUTCOME_JOINED: 3,
    outcomes.OUTCOME_JOIN_REQUESTED: 3,
    outcomes.OUTCOME_ALREADY_MEMBER: 3,
    outcomes.OUTCOME_ALREADY_PENDING: 3,
    outcomes.OUTCOME_JOIN_FAILED: 3,
    outcomes.OUTCOME_SKIPPED_CAP: 2,
}
_EDITORIAL: Final = 1

FLOW_TIMEOUT_ENV: Final = "FLOW_TIMEOUT_SECONDS"
# The end-of-run drain ends this long before the task worker's SIGKILL fuse.
CLOSE_RESERVE_S: Final = 15.0


def flow_deadline(started: float) -> float | None:
    """The monotonic instant ``close`` must be done by, or None when the
    process runs without a fuse (a manual run)."""
    try:
        timeout = float(os.environ.get(FLOW_TIMEOUT_ENV, "") or 0)
    except ValueError:
        return None
    return started + timeout - CLOSE_RESERVE_S if timeout > 0 else None


class FinalOutcomes:
    """Thread-safe per-run map of key -> the scout's final outcome."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_key: dict[str, str] = {}

    def note(self, key: str, outcome: str) -> None:
        """Keep ``outcome`` unless ``key`` already has a higher-priority one."""
        if not key:
            return
        with self._lock:
            current = self._by_key.get(key)
            if current is None or _rank(outcome) >= _rank(current):
                self._by_key[key] = outcome

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._by_key.get(key)

    def items(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._by_key.items())


def _rank(outcome: str) -> int:
    return _PRIORITY.get(outcome, _EDITORIAL)
