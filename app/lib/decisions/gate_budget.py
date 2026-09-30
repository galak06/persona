"""Per-run limits on Jev calls: count, wall-clock time and a circuit breaker.

``MAX_CALLS_PER_RUN`` caps spend. It does not cap time, and time is the scarce
resource: ig-engager already uses about 1512s of its 1800s fuse. So a run
also stops asking Jev when either of these happens:

* cumulative Jev wall-clock time reaches ``JEV_RUN_BUDGET_S``, or
* ``MAX_CONSECUTIVE_FAILURES`` calls fail in a row (the breaker trips and
  the gate stays off for the rest of the run: a down OpenRouter should cost
  two timeouts, not sixty).

One ``RunBudget`` is used from one thread at a time. That is the scan thread
under enforce, and the gate's single background worker under shadow.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Final

from lib.observability import get_logger

log = get_logger(__name__)

MAX_CALLS_PER_RUN: Final = 60
JEV_RUN_BUDGET_S: Final = 60.0
MAX_CONSECUTIVE_FAILURES: Final = 2

TRIP_CALLS: Final = "call_cap"
TRIP_TIME: Final = "time_budget"
TRIP_FAILURES: Final = "circuit_breaker"


# End-of-run drain wait for shadow work still queued or in flight.
DRAIN_TIMEOUT_S: Final = 10.0


def drain_timeout(remaining_s: float | None) -> float:
    """The shadow drain wait for a run with ``remaining_s`` left on its deadline.

    ``None`` means the run has no deadline. A passed deadline means no wait at
    all: the run's own teardown and last-run stamp outrank shadow bookkeeping.
    """
    if remaining_s is None:
        return DRAIN_TIMEOUT_S
    return max(0.0, min(DRAIN_TIMEOUT_S, remaining_s))


class RunBudget:
    """Counts calls, time and failures for one run; trips at most once."""

    def __init__(
        self,
        *,
        platform: str,
        max_calls: int = MAX_CALLS_PER_RUN,
        budget_s: float = JEV_RUN_BUDGET_S,
        max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.platform = platform
        self.max_calls = max_calls
        self.budget_s = budget_s
        self.max_consecutive_failures = max_consecutive_failures
        self._clock = clock
        self.calls = 0
        self.failures = 0
        self.consecutive_failures = 0
        self.spent_s = 0.0
        self.tripped: str | None = None

    def allow(self) -> bool:
        """True if one more call fits; records (and logs once) why not."""
        if self.tripped is not None:
            return False
        if self.consecutive_failures >= self.max_consecutive_failures:
            return self._trip(TRIP_FAILURES)
        if self.spent_s >= self.budget_s:
            return self._trip(TRIP_TIME)
        if self.calls >= self.max_calls:
            return self._trip(TRIP_CALLS)
        return True

    def start(self) -> float:
        """Count one call and return its start instant."""
        self.calls += 1
        return self._clock()

    def finish(self, started: float, *, ok: bool) -> None:
        """Charge the call's elapsed time and update the breaker."""
        self.spent_s += max(0.0, self._clock() - started)
        if ok:
            self.consecutive_failures = 0
        else:
            self.failures += 1
            self.consecutive_failures += 1

    def _trip(self, reason: str) -> bool:
        self.tripped = reason
        log.warning(
            "jev_gate_disabled_for_run",
            platform=self.platform,
            reason=reason,
            calls=self.calls,
            failures=self.failures,
            spent_s=round(self.spent_s, 2),
        )
        return False
