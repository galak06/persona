"""The Jev group-match gate as an fb-group-scout collaborator.

Implements ``lib.decisions.scout_hooks.GroupGate`` and mirrors the engager
gate (``engager_gate.JevPostGate``):

**Shadow must not slow the scout.** ``screen`` hands each card to a single
daemon worker (``ShadowWorker``) and returns the list untouched at once;
every Jev call and DB write runs there. ``close()`` drains the worker with a
bounded wait that never runs past the flow's own fuse
(``FLOW_TIMEOUT_SECONDS``, exported by the task worker).

Outcomes: each group gets ONE outcome per run, its final fate
(``scout_outcomes.FinalOutcomes``), stamped once at ``close()`` -- after
every decision, since the worker is FIFO -- and only on rows decided THIS
run: a group decided on an earlier run keeps the outcome it got then.

**Enforce blocks** (it must drop a card before the scout acts on it), bounded
by ``RunBudget``: a call cap, a cumulative wall-clock budget and a 2-failure
circuit breaker; once that trips, not even the DB lookup runs. A group
decided on an earlier run is never re-asked; enforce re-applies its stored
``would_skip``. A failed call is recorded as an ``error`` row with no
verdict.

Every public method never raises -- a classifier bug must never alter the
scout.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Mapping
from functools import partial
from typing import Final

from lib.decisions import decisions_db, outcomes
from lib.decisions.decisions_db import DecisionRecord
from lib.decisions.gate_budget import (
    DRAIN_TIMEOUT_S,
    JEV_RUN_BUDGET_S,
    RunBudget,
    drain_timeout,
)
from lib.decisions.group_gate import GroupGateVerdict, build_questions, evaluate_group
from lib.decisions.jev_types import questions_to_json
from lib.decisions.modes import MODE_ENFORCE, MODE_OFF, MODE_SHADOW, GateMode
from lib.decisions.post_gate import Decide
from lib.decisions.scout_hooks import G, item_key, outcome_for_join
from lib.decisions.scout_outcomes import FinalOutcomes, flow_deadline
from lib.decisions.shadow_worker import ShadowWorker, warn_if_drain_incomplete
from lib.observability import get_logger

log = get_logger(__name__)

PLATFORM: Final = "fb_group"
FLOW: Final = "fb-group-scout"
MODE_COLUMN: Final = "jev_group_gate"
# One Jev request per group. The scout joins at most 5/day, so 40 calls
# cover every realistic candidate pool.
MAX_CALLS_PER_RUN: Final = 40
# Cards handed to the shadow worker per run (most are DB lookups of groups
# seen on earlier runs, not Jev calls).
MAX_SCREENED_PER_RUN: Final = 200


class JevGroupGate:
    """Per-run gate state: brand, mode, focus, budget and the shadow worker."""

    def __init__(
        self,
        *,
        brand_id: str,
        mode: GateMode,
        brand_focus: str,
        flow: str = FLOW,
        max_calls: int = MAX_CALLS_PER_RUN,
        budget_s: float = JEV_RUN_BUDGET_S,
        drain_timeout_s: float = DRAIN_TIMEOUT_S,
        decide: Decide | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.brand_id = brand_id
        self.mode = mode
        self.brand_focus = brand_focus
        self.flow = flow
        self.drain_timeout_s = drain_timeout_s
        self.budget = RunBudget(
            platform=PLATFORM, max_calls=max_calls, budget_s=budget_s, clock=clock
        )
        self.recorded = 0
        self._decide_fn = decide
        self._clock = clock
        self._deadline = flow_deadline(clock())
        self._lock = threading.Lock()
        self._known: set[str] = set()  # keys whose row was written THIS run
        self._final = FinalOutcomes()
        self._verdicts: dict[str, bool] = {}  # this run's answer per key (dedupe)
        self._worker = ShadowWorker("jev-gate-groups")
        self._closed = False

    # --- GroupGate ------------------------------------------------------------

    def screen(self, groups: list[G]) -> list[G]:
        """Evaluate each group; drop gate skips only under enforce."""
        if self.mode == MODE_OFF:
            return groups
        try:
            if self.mode == MODE_SHADOW:
                for group in groups:
                    self._submit_decision(group)
                return groups
            skipped = {id(g) for g in groups if self._enforce_skip(g)}
        except Exception as exc:  # fail open: the scout's own list stands
            log.warning("jev_gate_error", stage="screen", error_type=type(exc).__name__)
            return groups
        return [g for g in groups if id(g) not in skipped] if skipped else groups

    def record(self, groups: Iterable[Mapping[str, object]], outcome: str) -> None:
        """Stamp the scout's decision on each group this run has a row for."""
        try:
            for group in groups:
                self._note(item_key(group), outcome)
        except Exception as exc:
            log.warning("jev_gate_error", stage="record", error_type=type(exc).__name__)

    def join_result(self, group: Mapping[str, object], status: str) -> None:
        """Stamp what the join attempt for ``group`` actually did."""
        try:
            self._note(item_key(group), outcome_for_join(group, status))
        except Exception as exc:
            log.warning("jev_gate_error", stage="join_result", error_type=type(exc).__name__)

    def close(self, timeout_s: float | None = None) -> None:
        """Drain shadow work (bounded, deadline-aware) and log the summary.

        Idempotent and never raises. The scout calls it once after its
        last-run stamp and again with ``timeout_s=0`` in ``finally``.
        ``None`` = the configured drain wait, clamped to the flow deadline.
        """
        if self._closed:
            return
        self._closed = True
        try:
            if timeout_s is None:
                remaining = None if self._deadline is None else self._deadline - self._clock()
                timeout_s = min(self.drain_timeout_s, drain_timeout(remaining))
            if self.mode == MODE_SHADOW:
                self._submit(self._stamp_all)  # FIFO: after every decision
            elif self.mode == MODE_ENFORCE:
                self._stamp_all()
            undrained = self._worker.drain(timeout_s)
            log.info(
                "jev_gate_run_summary",
                platform=PLATFORM,
                mode=self.mode,
                calls=self.budget.calls,
                failures=self.budget.failures,
                recorded=self.recorded,
                spent_s=round(self.budget.spent_s, 2),
                disabled_reason=self.budget.tripped,
                undrained=undrained,
            )
            with self._lock:  # an abandoned worker may still be writing these
                counts = {"screened": len(self._verdicts), "recorded": self.recorded}
            warn_if_drain_incomplete(undrained, platform=PLATFORM, **counts)
        except Exception as exc:
            log.warning("jev_gate_error", stage="close", error_type=type(exc).__name__)

    # --- scout thread -----------------------------------------------------------

    def _submit_decision(self, group: Mapping[str, object]) -> None:
        key = item_key(group)
        if not key or key in self._verdicts or len(self._verdicts) >= MAX_SCREENED_PER_RUN:
            return
        self._verdicts[key] = False
        self._submit(partial(self._decide, key, dict(group)))

    def _enforce_skip(self, group: Mapping[str, object]) -> bool:
        key = item_key(group)
        if not key:
            return False
        if key not in self._verdicts:
            if len(self._verdicts) >= MAX_SCREENED_PER_RUN:
                return False
            self._verdicts[key] = self._decide(key, group)
        return self._verdicts[key]

    def _note(self, key: str, outcome: str) -> None:
        self._final.note(key, outcome)

    def _submit(self, task: Callable[[], object]) -> None:
        self._worker.submit(task)

    # --- work (scout thread under enforce, worker thread under shadow) --------

    def _decide(self, key: str, group: Mapping[str, object]) -> bool:
        try:
            return self._decide_unsafe(key, group)
        except Exception as exc:
            log.warning("jev_gate_error", stage="decide", error_type=type(exc).__name__)
            return False

    def _decide_unsafe(self, key: str, group: Mapping[str, object]) -> bool:
        if self.budget.tripped is not None:
            return False  # tripped: not even the (2s-bounded) DB lookup
        stored = decisions_db.lookup_decision(self.brand_id, PLATFORM, key)
        if stored is not None:  # decided on an earlier run: never re-asked
            skip = self.mode == MODE_ENFORCE and stored  # re-apply that verdict
            if skip:
                self._note(key, outcomes.OUTCOME_SKIPPED_BY_GATE)
            return skip
        if not self.budget.allow():
            return False
        started = self.budget.start()
        verdict = evaluate_group(self.brand_focus, group, mode=self.mode, decide=self._decide_fn)
        self.budget.finish(started, ok=verdict is not None)
        if verdict is None:
            self._record_failure(key, started)
            return False
        self._record_verdict(key, verdict)
        if verdict.should_skip:
            self._note(key, outcomes.OUTCOME_SKIPPED_BY_GATE)
        return verdict.should_skip

    def _stamp_all(self) -> None:
        """Write each final outcome once, only on rows this run inserted."""
        with self._lock:
            known = set(self._known)
        for key, outcome in self._final.items():
            if key in known:
                decisions_db.record_outcome(key, outcome, brand_id=self.brand_id, platform=PLATFORM)

    def _insert(self, record: DecisionRecord) -> None:
        written = decisions_db.record_decision(record)
        with self._lock:
            self._known.add(record.item_key)
            self.recorded += int(written)

    def _record_failure(self, key: str, started: float) -> None:
        self._insert(
            DecisionRecord(
                brand_id=self.brand_id,
                flow=self.flow,
                platform=PLATFORM,
                item_key=key,
                questions=questions_to_json(build_questions(self.brand_focus)),
                answers={},
                mode=self.mode,
                would_skip=False,
                latency_ms=int(max(0.0, self._clock() - started) * 1000),
                error=decisions_db.ERROR_JEV_CALL_FAILED,
            )
        )

    def _record_verdict(self, key: str, verdict: GroupGateVerdict) -> None:
        log.info(
            "jev_group_gate_decision",
            mode=self.mode,
            item_key=key,
            match=verdict.match,
            off_topic=round(verdict.off_topic, 3),
            north_america=round(verdict.north_america, 3),
            members_can_comment=round(verdict.can_comment, 3),
            active=round(verdict.active, 3),
            would_skip=verdict.would_skip,
            skip_reasons=list(verdict.skip_reasons),
            latency_ms=verdict.result.latency_ms,
            cost_usd=verdict.result.cost_usd,
        )
        self._insert(
            DecisionRecord(
                brand_id=self.brand_id,
                flow=self.flow,
                platform=PLATFORM,
                item_key=key,
                questions=verdict.questions,
                answers=verdict.result.raw_answers,
                mode=self.mode,
                would_skip=verdict.would_skip,
                latency_ms=verdict.result.latency_ms,
                cost_usd=verdict.result.cost_usd,
            )
        )
