"""Wall-clock budget for one scan pass, derived from the worker's own fuse.

`scripts/task_worker.py` runs every flow under `subprocess.run(...,
timeout=timeout_seconds)`. That fuse is a SIGKILL, and a killed flow leaves
nothing behind: no `scan_funnel` line, no `last_run.json` stamp, no summary
in Telegram. `scripts/ig_engager.py` went seven days (2026-08-29 to
2026-09-05) without emitting a terminal summary for exactly that reason,
and because `_already_ran_today` only trips on a stamped `"success"`, a
killed pass also stopped suppressing the next one -- so every tick re-ran
the same doomed scan.

The fix is for the flow to know its own budget and stop itself first. The
worker exports the fuse as `FLOW_TIMEOUT_SECONDS`; `deadline_from_env`
turns it into a monotonic instant set a fixed RESERVE earlier, and
`lib/engagement/pipeline.py` checks it at source and post boundaries.

The reserve covers everything that still has to happen after the last
checkpoint passes:

    ~125s   the in-flight post visit (a 60s navigation timeout, the settle
            sleep, extraction, the like, and -- when the post qualifies --
            the drafter call and the comment submit)
     ~15s   browser/session teardown
     ~15s   funnel log + Telegram summary + `last_run.json` write
    ------
    ~155s, inside the 180s reserve.

What the reserve deliberately does NOT have to cover is the 120-180s pacing
sleep `lib/engagement/comment_submit.py` takes after a posted comment:
`DeadlineAwareRateTracker` drops it once the deadline has passed, because
that delay exists to space out CONSECUTIVE comments and a pass that is
shutting down has nothing left to pace against.

This is a fallback, not a shortener. 180s of the 1800s ig-engager fuse
leaves 1620s of scanning, and the healthy full pass measured on 2026-09-05
took 1512s -- a run that would finish on its own never reaches the deadline.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from lib.engagement.collaborators import RateTracker

FLOW_TIMEOUT_ENV = "FLOW_TIMEOUT_SECONDS"
RESERVE_SECONDS = 180.0
# A budget under twice the reserve would leave no room to work in, so a small
# or odd budget gives up half of itself rather than all of it. Nothing in
# production hits this today (ig-engager's fuse is 1800s); it exists so a
# hand-set `--timeout` can never produce an already-expired deadline.
_MAX_RESERVE_FRACTION = 0.5

# `ScanReport.stopped_reason` values. `None` means the pass ran to the end.
STOPPED_DEADLINE = "deadline"
STOPPED_RATE_LIMIT = "rate_limit"


@dataclass(frozen=True)
class ScanDeadline:
    """The monotonic instant after which a pass must not start new work.

    `now` is injected so tests can drive the clock; production always gets
    `time.monotonic`, which (unlike wall-clock time) cannot jump backwards
    over a scan that runs for half an hour.
    """

    deadline_at: float
    budget_seconds: float
    reserve_seconds: float
    now: Callable[[], float] = time.monotonic

    def expired(self) -> bool:
        """True once the pass must stop at its next safe checkpoint."""
        return self.now() >= self.deadline_at

    def remaining(self) -> float:
        """Seconds left before the deadline. Negative once it has passed."""
        return self.deadline_at - self.now()


def deadline_from_env(
    env: Mapping[str, str] | None = None,
    *,
    now: Callable[[], float] = time.monotonic,
) -> ScanDeadline | None:
    """Build this pass's deadline from `FLOW_TIMEOUT_SECONDS`, or None.

    None means "no budget was declared" -- a manual CLI invocation, or a
    worker image predating the export. A malformed or non-positive value is
    treated the same way on purpose: a broken budget must never truncate a
    run that would otherwise have finished.
    """
    source = os.environ if env is None else env
    try:
        budget = float(source.get(FLOW_TIMEOUT_ENV, ""))
    except (TypeError, ValueError):
        return None
    if budget <= 0:
        return None
    reserve = min(RESERVE_SECONDS, budget * _MAX_RESERVE_FRACTION)
    return ScanDeadline(
        deadline_at=now() + budget - reserve,
        budget_seconds=budget,
        reserve_seconds=reserve,
        now=now,
    )


class DeadlineAwareRateTracker:
    """`RateTracker` decorator that drops pacing sleeps past the deadline.

    Everything else is forwarded untouched -- the daily budgets are the
    platform's business and a deadline must never buy extra actions. Only
    `wait_random_delay` is conditional, and only in the one direction that
    cannot cause a burst: the delay it skips is the trailing one, after the
    last comment of a pass that is already shutting down.

    Wrapping the collaborator is what keeps the pacing sleep out of the
    reserve without threading a deadline through `post_processor` ->
    `inline_comment` -> `comment_submit`.
    """

    def __init__(self, inner: RateTracker, deadline: ScanDeadline | None) -> None:
        self._inner = inner
        self._deadline = deadline

    def can_act(self, platform: str, action: str) -> bool:
        return self._inner.can_act(platform, action)

    def record_action(self, platform: str, action: str) -> int:
        return self._inner.record_action(platform, action)

    def wait_random_delay(self, platform: str, action: str) -> None:
        if self._deadline is not None and self._deadline.expired():
            return
        self._inner.wait_random_delay(platform, action)
