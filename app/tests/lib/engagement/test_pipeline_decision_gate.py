"""The Jev `decision_gate` seam in the single-pass comment step.

The load-bearing property: a SHADOW gate observes every drafted post but
can never change what the scan does -- same comments, same likes, same
report as a run with no gate at all, even when the gate would skip every
post or blows up. Only an ENFORCING gate may skip the draft.

Uses the real `JevPostGate` with a fake `decide` and in-memory
`decisions_db` so the pipeline -> gate -> store path is exercised end to
end without network or database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict

import pytest

from lib.decisions import decisions_db, jev_client
from lib.decisions.engager_gate import JevPostGate
from lib.decisions.jev_types import ChoiceAnswer, JevResult, JsonDict, NoulAnswer, Question
from lib.decisions.modes import GateMode
from lib.engagement.adapters.fake import FakeAdapter
from lib.engagement.pipeline import ScanReport, run_outbound_scan
from lib.engagement.post import Post
from tests.lib.engagement._pipeline_fakes import (
    FakeDrafter,
    FakeIterateOnceDedup,
    FakeLog,
    FakeRateTracker,
    make_ig_posts,
    make_policy,
    make_src,
    stub_now,
    stub_score,
)

_SKIP_ALL = JevResult(
    answers={
        "relevant": NoulAnswer(noul=0.0),
        "value": ChoiceAnswer(choice="nothing_to_add", confidence=1.0),
        "unsafe": NoulAnswer(noul=1.0),
    },
    raw_answers={},
    model="typesafe/jev-1.13",
    latency_ms=1,
)


class _Store:
    def __init__(self) -> None:
        self.decisions: list[decisions_db.DecisionRecord] = []
        self.outcomes: list[tuple[str, str]] = []


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    s = _Store()

    def _record(rec: decisions_db.DecisionRecord) -> bool:
        s.decisions.append(rec)
        return True

    def _outcome(key: str, outcome: str, **_kw: object) -> bool:
        s.outcomes.append((key, outcome))
        return True

    def _decide(_state: str | JsonDict, _q: Mapping[str, Question]) -> JevResult:
        return _SKIP_ALL

    monkeypatch.setattr(decisions_db, "has_decision", lambda *_a: False)
    monkeypatch.setattr(decisions_db, "record_decision", _record)
    monkeypatch.setattr(decisions_db, "record_outcome", _outcome)
    monkeypatch.setattr(jev_client, "decide", _decide)
    return s


class _ExplodingGate:
    def before_draft(self, post: Post) -> bool:
        raise RuntimeError("classifier bug")

    def after_draft(self, post: Post, *, drafted: bool, reason: str | None) -> None:
        raise RuntimeError("classifier bug")


def _scan(
    gate: object | None, *, engage: bool = True
) -> tuple[ScanReport, FakeAdapter, FakeDrafter]:
    adapter = FakeAdapter("instagram", [make_src("s1")], {"s1": make_ig_posts(3)})
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
    )
    return report, adapter, drafter


def _gate(mode: GateMode) -> JevPostGate:
    return JevPostGate(
        brand_id="b", flow="ig-engager", platform="instagram", mode=mode, brand_focus="dog food"
    )


def test_shadow_gate_never_alters_the_run(store: _Store) -> None:
    baseline, base_adapter, base_drafter = _scan(None)
    shadowed, adapter, drafter = _scan(_gate("shadow"))

    assert asdict(shadowed) == asdict(baseline)
    assert adapter.comments == base_adapter.comments
    assert len(drafter.calls) == len(base_drafter.calls) == 3
    # ...while still logging a would-skip verdict + the real outcome per post.
    assert [d.would_skip for d in store.decisions] == [True, True, True]
    assert [o for _k, o in store.outcomes] == ["engaged", "engaged", "engaged"]


def test_shadow_gate_records_declines(store: _Store) -> None:
    _scan(_gate("shadow"), engage=False)
    assert [o for _k, o in store.outcomes] == ["declined"] * 3


def test_exploding_gate_never_alters_the_run() -> None:
    baseline, base_adapter, _ = _scan(None)
    report, adapter, _ = _scan(_ExplodingGate())
    assert asdict(report) == asdict(baseline)
    assert adapter.comments == base_adapter.comments


def test_enforce_gate_skips_the_draft(store: _Store) -> None:
    report, adapter, drafter = _scan(_gate("enforce"))
    assert drafter.calls == []
    assert adapter.comments == []
    assert report.comments_declined == 3
    assert [o for _k, o in store.outcomes] == ["skipped_by_gate"] * 3
