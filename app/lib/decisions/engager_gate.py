"""The Jev post gate as an engagement-pipeline collaborator.

The gate satisfies ``lib.engagement.collaborators.DecisionGate`` structurally.
The pipeline calls it at two points:

* ``before_draft``, right before the drafter would run. By then the comment
  gate, the quota check and the claim check have all passed, so a post that
  could never be commented costs no Jev call.
* ``after_draft``, once the drafter has decided.

**Shadow must not block the scan.** Every piece of DB and Jev work goes to a
single background worker, and ``before_draft`` returns False immediately.
The single worker runs tasks FIFO, so a post's outcome stamp always runs
after that post's decision. The outcome is also parked in
``_pending_outcomes`` and folded into the INSERT, so it is not lost whichever
side lands first. ``close()`` drains the worker with a bounded wait at the
end of the run.

**Enforce blocks.** It is the only mode whose answer matters. It runs inline,
bounded by ``RunBudget``: a call cap, a cumulative wall-clock budget and a
2-failure circuit breaker. Both methods never raise.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from typing import Final, Protocol

from lib.decisions import decisions_db
from lib.decisions.decisions_db import DecisionRecord
from lib.decisions.gate_budget import JEV_RUN_BUDGET_S, MAX_CALLS_PER_RUN, RunBudget
from lib.decisions.jev_types import questions_to_json
from lib.decisions.modes import MODE_ENFORCE, MODE_OFF, MODE_SHADOW, GateMode
from lib.decisions.post_gate import PostGateVerdict, build_questions, evaluate_post
from lib.observability import get_logger

log = get_logger(__name__)

# End-of-run drain wait for shadow work still queued or in flight.
DRAIN_TIMEOUT_S: Final = 10.0

# Mirrors lib.draft_helper's DRAFT_FAILED / DRAFT_BLANK (pinned by a test).
# Those are upstream failures, not an editorial decision. Everything else
# that yields no text -- agent_declined, or a drafter without
# `last_outcome` -- is a decline. voice_validation_failed only happens after
# the drafter chose to engage, so it counts as engaged.
DRAFTER_ERROR_REASONS: Final = frozenset({"draft_failed", "draft_blank"})
VOICE_FAILED_REASON: Final = "voice_validation_failed"


class Post(Protocol):
    """The slice of `lib.engagement.post.Post` the gate reads (structural, so
    this package never imports `lib.engagement`)."""

    @property
    def post_id(self) -> str: ...
    @property
    def post_url(self) -> str: ...
    @property
    def text(self) -> str: ...
    @property
    def source_name(self) -> str | None: ...


@dataclass(frozen=True)
class _Snapshot:
    """What the background worker needs from a post (Posts are not ours)."""

    key: str
    text: str
    source_name: str | None


def item_key(post: Post) -> str:
    """The stable key a post's decision and outcome are filed under."""
    return post.post_url or post.post_id


def outcome_for(*, drafted: bool, reason: str | None) -> str:
    """Map the drafter's result to a ``jev_decisions.outcome`` value."""
    if drafted or reason == VOICE_FAILED_REASON:
        return decisions_db.OUTCOME_ENGAGED
    if reason in DRAFTER_ERROR_REASONS:
        return decisions_db.OUTCOME_DRAFTER_ERROR
    return decisions_db.OUTCOME_DECLINED


class JevPostGate:
    """Per-run gate state: brand, mode, focus, budget and the shadow worker."""

    def __init__(
        self,
        *,
        brand_id: str,
        flow: str,
        platform: str,
        mode: GateMode,
        brand_focus: str,
        max_calls: int = MAX_CALLS_PER_RUN,
        budget_s: float = JEV_RUN_BUDGET_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.brand_id = brand_id
        self.flow = flow
        self.platform = platform
        self.mode = mode
        self.brand_focus = brand_focus
        self.budget = RunBudget(
            platform=platform, max_calls=max_calls, budget_s=budget_s, clock=clock
        )
        self.recorded = 0
        self._lock = threading.Lock()
        self._known: set[str] = set()  # keys with a row: decided this run, or found stored
        self._pending_outcomes: dict[str, str] = {}
        self._submitted = 0
        self._executor: ThreadPoolExecutor | None = None
        self._futures: list[Future[object]] = []

    # --- DecisionGate -------------------------------------------------------

    def before_draft(self, post: Post) -> bool:
        """True only when an enforcing gate says skip. Shadow never blocks."""
        try:
            if self.mode == MODE_OFF:
                return False
            snap = _Snapshot(key=item_key(post), text=post.text, source_name=post.source_name)
            if self.mode == MODE_SHADOW:
                if self._submitted < self.budget.max_calls:
                    self._submitted += 1
                    self._submit(lambda: self._decide(snap))
                return False
            return self._decide(snap)
        except Exception as exc:  # a classifier bug must never stop a comment
            log.warning("jev_gate_error", stage="before_draft", error_type=type(exc).__name__)
            return False

    def after_draft(self, post: Post, *, drafted: bool, reason: str | None) -> None:
        """Stamp what the drafter did -- only for posts this run has a row for."""
        try:
            key = item_key(post)
            outcome = outcome_for(drafted=drafted, reason=reason)
            with self._lock:
                self._pending_outcomes[key] = outcome
            if self.mode == MODE_SHADOW:
                self._submit(lambda: self._stamp(key))
            else:
                self._stamp(key)
        except Exception as exc:
            log.warning("jev_gate_error", stage="after_draft", error_type=type(exc).__name__)

    def close(self, timeout_s: float = DRAIN_TIMEOUT_S) -> None:
        """Drain shadow work (bounded) and log the run's gate summary."""
        undrained = 0
        if self._executor is not None:
            _done, not_done = wait(self._futures, timeout=timeout_s)
            undrained = len(not_done)
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        log.info(
            "jev_gate_run_summary",
            platform=self.platform,
            mode=self.mode,
            calls=self.budget.calls,
            failures=self.budget.failures,
            recorded=self.recorded,
            spent_s=round(self.budget.spent_s, 2),
            disabled_reason=self.budget.tripped,
            undrained=undrained,
        )

    # --- work (scan thread under enforce, worker thread under shadow) -------

    def _submit(self, task: Callable[[], object]) -> None:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jev-gate")
        self._futures.append(self._executor.submit(task))

    def _decide(self, snap: _Snapshot) -> bool:
        try:
            return self._decide_unsafe(snap)
        except Exception as exc:
            log.warning("jev_gate_error", stage="decide", error_type=type(exc).__name__)
            return False

    def _decide_unsafe(self, snap: _Snapshot) -> bool:
        stored = decisions_db.lookup_decision(self.brand_id, self.platform, snap.key)
        if stored is not None:
            with self._lock:
                self._known.add(snap.key)
            # Enforce re-applies the verdict stored by an earlier run.
            skip = self.mode == MODE_ENFORCE and stored
            if skip:
                self._skipped(snap.key)
            return skip
        if not self.budget.allow():
            return False
        started = self.budget.start()
        verdict = evaluate_post(
            self.platform, self.brand_focus, snap.text, mode=self.mode, source_name=snap.source_name
        )
        self.budget.finish(started, ok=verdict is not None)
        if verdict is None:
            self._record_failure(snap.key, started)
            return False
        self._record_verdict(snap.key, verdict)
        if verdict.should_skip:
            self._skipped(snap.key)
        return verdict.should_skip

    def _skipped(self, key: str) -> None:
        with self._lock:
            self._pending_outcomes[key] = decisions_db.OUTCOME_SKIPPED_BY_GATE
        self._stamp(key)

    def _stamp(self, key: str) -> None:
        with self._lock:
            if key not in self._known:
                return  # no row this run: the outcome waits in _pending_outcomes
            outcome = self._pending_outcomes.pop(key, None)
        if outcome is not None:
            decisions_db.record_outcome(
                key, outcome, brand_id=self.brand_id, platform=self.platform
            )

    def _insert(self, record: DecisionRecord) -> None:
        with self._lock:
            pending = self._pending_outcomes.pop(record.item_key, None)
        written = decisions_db.record_decision(replace(record, outcome=pending))
        with self._lock:
            self._known.add(record.item_key)
            self.recorded += int(written)

    def _record_failure(self, key: str, started: float) -> None:
        self._insert(
            DecisionRecord(
                brand_id=self.brand_id,
                flow=self.flow,
                platform=self.platform,
                item_key=key,
                questions=questions_to_json(build_questions(self.brand_focus)),
                answers={},
                mode=self.mode,
                would_skip=False,
                latency_ms=int(max(0.0, time.monotonic() - started) * 1000),
                error=decisions_db.ERROR_JEV_CALL_FAILED,
            )
        )

    def _record_verdict(self, key: str, verdict: PostGateVerdict) -> None:
        log.info(
            "jev_post_gate_decision",
            platform=self.platform,
            mode=self.mode,
            item_key=key,
            relevant=round(verdict.relevant, 3),
            value=verdict.value,
            unsafe=round(verdict.unsafe, 3),
            would_skip=verdict.would_skip,
            skip_reasons=list(verdict.skip_reasons),
            latency_ms=verdict.result.latency_ms,
            cost_usd=verdict.result.cost_usd,
        )
        self._insert(
            DecisionRecord(
                brand_id=self.brand_id,
                flow=self.flow,
                platform=self.platform,
                item_key=key,
                questions=verdict.questions,
                answers=verdict.result.raw_answers,
                mode=self.mode,
                would_skip=verdict.would_skip,
                latency_ms=verdict.result.latency_ms,
                cost_usd=verdict.result.cost_usd,
            )
        )
