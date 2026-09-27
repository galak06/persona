"""Unit tests for the fb-group-scout Jev collaborator (`lib.decisions.scout_gate`),
its seam helpers (`scout_hooks`), its daemon worker (`shadow_worker`) and its
factory (`gate_factory.build_group_gate`).

No network and no DB: `decide` is a fake and the three gate-path repository
functions are an in-memory store. (`tests._jev_fakes.FakeStore` has the same
shape, but importing it pulls in `lib.engagement`, which would take these
tests off the CI mypy list.)
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any

import pytest

from lib.decisions import decisions_db, outcomes
from lib.decisions.jev_types import JevResult, JsonDict, Question
from lib.decisions.scout_gate import JevGroupGate
from lib.decisions.scout_hooks import (
    item_key,
)
from lib.decisions.scout_outcomes import CLOSE_RESERVE_S, flow_deadline
from tests.test_group_gate import FakeDecide, _answers


class FakeStore:
    """In-memory `record_decision` / `lookup_decision` / `record_outcome`."""

    def __init__(self) -> None:
        self.stored: dict[str, bool] = {}
        self.decisions: list[decisions_db.DecisionRecord] = []
        self.outcomes: list[tuple[str, str]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeStore:
        monkeypatch.setattr(decisions_db, "lookup_decision", self._lookup)
        monkeypatch.setattr(decisions_db, "record_decision", self._record)
        monkeypatch.setattr(decisions_db, "record_outcome", self._outcome)
        return self

    def _lookup(self, _brand: str, _platform: str, key: str) -> bool | None:
        return self.stored.get(key)

    def _record(self, record: decisions_db.DecisionRecord) -> bool:
        self.decisions.append(record)
        self.stored[record.item_key] = record.would_skip
        return True

    def _outcome(self, key: str, outcome: str, **_kw: object) -> bool:
        self.outcomes.append((key, outcome))
        return True

    def final_outcomes(self) -> dict[str, str | None]:
        """Per key: the outcome as it would stand in the table."""
        out: dict[str, str | None] = {d.item_key: d.outcome for d in self.decisions}
        out.update(dict(self.outcomes))
        return out


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    return FakeStore().install(monkeypatch)


def _card(i: int, score: int = 50, privacy: str = "public") -> dict[str, Any]:
    url = f"https://www.facebook.com/groups/G{i}/"
    return {"url": url, "name": f"g{i}", "score": score, "privacy": privacy}


def _gate(decide: Any, mode: Any = "shadow", **kw: Any) -> JevGroupGate:
    return JevGroupGate(brand_id="b1", mode=mode, brand_focus="dog food", decide=decide, **kw)


class SlowDecide(FakeDecide):
    def __init__(self, delay_s: float, **kw: Any) -> None:
        super().__init__(_answers(**kw))
        self.delay_s = delay_s

    def __call__(
        self, state: str | JsonDict, questions: Mapping[str, Question]
    ) -> JevResult | None:
        threading.Event().wait(self.delay_s)  # not time.sleep: scout tests patch that
        return super().__call__(state, questions)


def test_shadow_screen_returns_at_once_and_stamps_this_runs_rows(store: FakeStore) -> None:
    decide = SlowDecide(0.2, match="off_topic", match_confidence=1.0)  # would skip all
    gate = _gate(decide)
    groups = [_card(i) for i in range(3)]
    started = time.monotonic()
    assert gate.screen(groups) is groups
    gate.record(groups[:2], outcomes.OUTCOME_SKIPPED_LOW_SCORE)
    gate.join_result(groups[2], "clicked:join")
    assert time.monotonic() - started < 0.1  # Jev is off the scout's thread
    gate.close()
    assert len(store.decisions) == 3
    assert all(r.would_skip and r.platform == "fb_group" for r in store.decisions)
    assert store.final_outcomes() == {
        item_key(groups[0]): "skipped_low_score",
        item_key(groups[1]): "skipped_low_score",
        item_key(groups[2]): "joined",
    }


def test_outcomes_never_overwrite_an_earlier_runs_row(store: FakeStore) -> None:
    earlier = item_key(_card(1))
    store.stored[earlier] = False
    decide = FakeDecide(_answers())
    gate = _gate(decide, mode="enforce")
    assert gate.screen([_card(1)])
    gate.record([_card(1)], outcomes.OUTCOME_SKIPPED_CAP)
    gate.close()
    assert decide.calls == [] and store.outcomes == []


def test_close_respects_the_flow_deadline(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLOW_TIMEOUT_SECONDS", "1")  # already inside the reserve
    gate = _gate(SlowDecide(1.0))
    gate.screen([_card(1)])
    started = time.monotonic()
    gate.close()
    assert time.monotonic() - started < 0.3
    assert flow_deadline(100.0) == pytest.approx(100.0 + 1 - CLOSE_RESERVE_S)
    monkeypatch.setenv("FLOW_TIMEOUT_SECONDS", "junk")
    assert flow_deadline(0.0) is None
    monkeypatch.delenv("FLOW_TIMEOUT_SECONDS")
    assert flow_deadline(0.0) is None
    _join_worker(gate)


def _join_worker(gate: JevGroupGate) -> None:
    """Let an abandoned call land while the test's fakes are still installed."""
    thread = gate._worker._thread
    if thread is not None:
        thread.join(timeout=5.0)


def test_enforce_drops_skips_keeps_order_and_reuses_verdicts(store: FakeStore) -> None:
    class ByName(FakeDecide):
        def __call__(self, state: Any, questions: Any) -> JevResult | None:
            self.answers = _answers(active=0.0 if state["name"] == "g2" else 2.0)
            return super().__call__(state, questions)

    decide = ByName(None)
    gate = _gate(decide, mode="enforce")
    groups = [_card(1), _card(2), _card(3)]
    assert [g["name"] for g in gate.screen(groups)] == ["g1", "g3"]
    assert gate.screen([_card(2)]) == []  # same run: cached, not re-asked
    assert len(decide.calls) == 3
    gate.close()
    assert store.final_outcomes()[item_key(groups[1])] == "skipped_by_gate"

    again = FakeDecide(_answers())  # a later run re-applies the stored verdict
    assert [g["name"] for g in _gate(again, mode="enforce").screen(groups[:2])] == ["g1"]
    assert again.calls == []


def test_breaker_opens_after_two_failures_and_records_error_rows(store: FakeStore) -> None:
    decide = FakeDecide(None)
    gate = _gate(decide, mode="enforce")
    groups = [_card(i) for i in range(5)]
    assert gate.screen(groups) is groups  # a failing Jev never drops a group
    assert len(decide.calls) == 2 and gate.budget.tripped == "circuit_breaker"
    assert {r.error for r in store.decisions} == {decisions_db.ERROR_JEV_CALL_FAILED}
    assert not any(r.would_skip or r.answers for r in store.decisions)


def test_time_budget_and_call_cap(store: FakeStore) -> None:
    gate = _gate(SlowDecide(0.06), mode="enforce", budget_s=0.05)
    gate.screen([_card(i) for i in range(4)])
    assert gate.budget.calls == 1 and gate.budget.tripped == "time_budget"

    capped = FakeDecide(_answers())
    _gate(capped, mode="enforce", max_calls=2).screen([_card(i + 10) for i in range(4)])
    assert len(capped.calls) == 2


def test_everything_exploding_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> Any:
        raise RuntimeError("down")

    for name in ("record_decision", "lookup_decision", "record_outcome"):
        monkeypatch.setattr(decisions_db, name, boom)
    for mode in ("shadow", "enforce"):
        gate = _gate(boom, mode=mode)
        groups = [_card(1), _card(2)]
        assert gate.screen(groups) is groups
        gate.record(groups, outcomes.OUTCOME_SKIPPED_CAP)
        gate.join_result(groups[0], "clicked:join")
        gate.close()


def test_off_mode_does_nothing(store: FakeStore) -> None:
    decide = FakeDecide(_answers())
    gate = _gate(decide, mode="off")
    groups = [_card(1)]
    assert gate.screen(groups) is groups
    gate.close()
    assert decide.calls == [] and store.decisions == []


def test_close_is_idempotent_and_zero_wait_close_never_waits(store: FakeStore) -> None:
    gate = _gate(SlowDecide(0.5))
    gate.screen([_card(1), _card(2)])
    started = time.monotonic()
    gate.close(timeout_s=0.0)
    gate.close()  # already closed: no second drain, no wait
    assert time.monotonic() - started < 0.3
    _join_worker(gate)


def test_each_group_is_stamped_once_with_its_final_outcome(store: FakeStore) -> None:
    gate = _gate(FakeDecide(_answers()))
    card = _card(1)
    gate.screen([card])
    gate.record([dict(card)], outcomes.OUTCOME_SKIPPED_LOW_SCORE)
    gate.join_result(card, "clicked:join group")
    gate.close()
    assert store.outcomes == [(item_key(card), "joined")]


def test_enforce_after_a_trip_skips_even_the_db_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore().install(monkeypatch)
    lookups: list[str] = []
    real_lookup = decisions_db.lookup_decision

    def counting_lookup(brand: str, platform: str, key: str) -> bool | None:
        lookups.append(key)
        return real_lookup(brand, platform, key)

    monkeypatch.setattr(decisions_db, "lookup_decision", counting_lookup)
    gate = _gate(FakeDecide(None), mode="enforce")
    assert gate.screen([_card(i) for i in range(6)])
    assert gate.budget.tripped == "circuit_breaker"
    assert len(lookups) == 3  # two failed calls, then the lookup that tripped it
    assert len(store.decisions) == 2
