"""The Jev `decision_gate` seam in the single-pass comment step.

The load-bearing property is that a SHADOW gate observes every drafted post
but can never change what the scan does. The run must be identical to a run
with no gate at all, meaning the same sources visited, comments, likes and
report. That holds when:

* the gate would skip every post;
* the gate blows up;
* Jev is SLOW and the pass runs against a real wall-clock `ScanDeadline`
  (ig-engager has less than 5 minutes of fuse headroom, so a blocking
  classifier would truncate the scan).

Only an ENFORCING gate may skip the draft, and it is bounded by the run
budget.

These tests use the real `JevPostGate` with a fake `decide` and an in-memory
`decisions_db`, so the pipeline -> gate -> store path runs end to end with no
network or database.
"""

from __future__ import annotations

import time
from dataclasses import asdict

import pytest

from lib.decisions.engager_gate import JevPostGate
from lib.decisions.modes import GateMode
from lib.engagement.adapters.fake import FakeAdapter
from lib.engagement.pipeline import ScanReport, run_outbound_scan
from lib.engagement.post import Post
from lib.engagement.scan_deadline import ScanDeadline
from tests._jev_fakes import SKIP_ALL, FakeJev, FakeStore, join_gate_threads
from tests.lib.engagement._pipeline_fakes import (
    FakeDrafter,
    FakeIterateOnceDedup,
    FakeLog,
    FakeRateTracker,
    make_policy,
    make_post,
    make_src,
    stub_now,
    stub_score,
)


class _ExplodingGate:
    def before_draft(self, post: Post) -> bool:
        raise RuntimeError("classifier bug")

    def after_draft(self, post: Post, *, drafted: bool, reason: str | None) -> None:
        raise RuntimeError("classifier bug")


def _adapter(sources: int = 1, posts: int = 3) -> FakeAdapter:
    """``sources`` x ``posts`` comment candidates, every post id unique."""
    srcs = [make_src(f"s{i}") for i in range(sources)]
    return FakeAdapter(
        "instagram",
        srcs,
        {
            s.id: [
                make_post(f"{s.id}p{j}", f"food question {j}?", source_id=s.id)
                for j in range(posts)
            ]
            for s in srcs
        },
    )


def _scan(
    gate: object | None,
    *,
    engage: bool = True,
    sources: int = 1,
    deadline: ScanDeadline | None = None,
) -> tuple[ScanReport, FakeAdapter, FakeDrafter]:
    adapter = _adapter(sources)
    drafter = FakeDrafter(engage=engage)
    report = run_outbound_scan(
        adapter,
        make_policy(),
        dedup=FakeIterateOnceDedup(),
        rate_tracker=FakeRateTracker(),
        drafter=drafter,
        log=FakeLog(),
        now_iso=stub_now,
        score_relevance=stub_score,
        inline_comment=True,
        decision_gate=gate,  # type: ignore[arg-type]
        deadline=deadline,
    )
    return report, adapter, drafter


def _gate(mode: GateMode) -> JevPostGate:
    return JevPostGate(
        brand_id="b", flow="ig-engager", platform="instagram", mode=mode, brand_focus="dog food"
    )


def test_shadow_gate_never_alters_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore().install(monkeypatch)
    FakeJev(SKIP_ALL).install(monkeypatch)
    baseline, base_adapter, base_drafter = _scan(None)
    gate = _gate("shadow")
    shadowed, adapter, drafter = _scan(gate)
    gate.close()

    assert asdict(shadowed) == asdict(baseline)
    assert adapter.comments == base_adapter.comments
    assert len(drafter.calls) == len(base_drafter.calls) == 3
    # ...while still logging a would-skip verdict + the real outcome per post.
    assert [d.would_skip for d in store.decisions] == [True, True, True]
    assert set(store.final_outcomes().values()) == {"engaged"}


def test_shadow_gate_records_declines(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore().install(monkeypatch)
    FakeJev(SKIP_ALL).install(monkeypatch)
    gate = _gate("shadow")
    _scan(gate, engage=False)
    gate.close()
    assert list(store.final_outcomes().values()) == ["declined"] * 3


def test_slow_jev_in_shadow_does_not_truncate_a_deadlined_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4 sources x 3 candidates, Jev taking 0.25s each: a blocking gate would
    spend ~3s against a 1s deadline and stop after the first source."""
    FakeStore().install(monkeypatch)
    FakeJev(SKIP_ALL, sleep_s=0.25).install(monkeypatch)

    def _deadline() -> ScanDeadline:
        return ScanDeadline(
            deadline_at=time.monotonic() + 1.0, budget_seconds=1.0, reserve_seconds=0.0
        )

    baseline, base_adapter, _ = _scan(None, sources=4, deadline=_deadline())
    gate = _gate("shadow")
    started = time.monotonic()
    shadowed, adapter, _ = _scan(gate, sources=4, deadline=_deadline())
    scan_s = time.monotonic() - started
    gate.close(timeout_s=0.1)
    join_gate_threads()

    assert baseline.sources_visited == shadowed.sources_visited == 4
    assert shadowed.stopped_reason is None
    assert adapter.comments == base_adapter.comments
    assert asdict(shadowed) == asdict(baseline)
    assert scan_s < 0.5  # the scan thread never waited on Jev


def test_exploding_gate_never_alters_the_run() -> None:
    baseline, base_adapter, _ = _scan(None)
    report, adapter, _ = _scan(_ExplodingGate())
    assert asdict(report) == asdict(baseline)
    assert adapter.comments == base_adapter.comments


def test_enforce_gate_skips_the_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore().install(monkeypatch)
    FakeJev(SKIP_ALL).install(monkeypatch)
    report, adapter, drafter = _scan(_gate("enforce"))
    assert drafter.calls == []
    assert adapter.comments == []
    assert report.comments_declined == 3
    assert [o for _k, o in store.outcomes] == ["skipped_by_gate"] * 3


def test_enforce_with_jev_down_costs_two_calls_then_behaves_like_no_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeStore().install(monkeypatch)
    jev = FakeJev(None).install(monkeypatch)
    baseline, base_adapter, _ = _scan(None, sources=3)
    report, adapter, _ = _scan(_gate("enforce"), sources=3)
    assert len(jev.calls) == 2  # circuit breaker
    assert asdict(report) == asdict(baseline)
    assert adapter.comments == base_adapter.comments
