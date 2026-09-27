"""Unit tests for the fb-group-scout gate's pieces that need no gate
instance: the seam helpers (`scout_hooks`), the per-run final-outcome map
(`scout_outcomes`), the daemon worker (`shadow_worker`) and the factory
(`gate_factory.build_group_gate`). Split from `test_scout_gate.py` (300-line cap).
"""

from __future__ import annotations

import threading
import time
from functools import partial
from typing import Any

import pytest

from lib import brands_db
from lib.decisions import gate_factory, outcomes
from lib.decisions.gate_factory import build_group_gate
from lib.decisions.scout_hooks import (
    NullGroupGate,
    dropped,
    item_key,
    outcome_for_join,
    screen_ranked,
)
from lib.decisions.scout_outcomes import FinalOutcomes
from lib.decisions.shadow_worker import ShadowWorker
from tests.test_scout_gate import _card


def test_item_key_and_join_outcomes() -> None:
    assert item_key({"url": " https://www.facebook.com/groups/AbC/ "}) == (
        "https://www.facebook.com/groups/abc"
    )
    assert item_key({}) == ""
    pub, priv = {"privacy": "public"}, {"privacy": "private"}
    assert outcome_for_join(pub, "clicked:join group") == outcomes.OUTCOME_JOINED
    assert outcome_for_join(priv, "clicked:request") == outcomes.OUTCOME_JOIN_REQUESTED
    assert outcome_for_join(pub, "already_joined") == outcomes.OUTCOME_ALREADY_MEMBER
    assert outcome_for_join(pub, "already_pending") == outcomes.OUTCOME_ALREADY_PENDING
    assert outcome_for_join(pub, "not_found") == outcomes.OUTCOME_JOIN_FAILED
    assert outcome_for_join(pub, "error") == outcomes.OUTCOME_JOIN_FAILED


def test_screen_ranked_keeps_scout_order_and_identity() -> None:
    groups = [_card(1, 10), _card(2, 90), _card(3, 50)]
    assert screen_ranked(NullGroupGate(), groups, lambda g: g["score"]) is groups

    class DropBest(NullGroupGate):
        def screen(self, gs: list[Any]) -> list[Any]:
            return gs[1:]  # drops the best-ranked, proving rank order was used

    kept = screen_ranked(DropBest(), groups, lambda g: g["score"])
    assert [g["name"] for g in kept] == ["g1", "g3"]
    assert dropped(groups, kept) == [groups[1]]


def test_shadow_worker_is_fifo_daemon_and_bounded() -> None:
    worker = ShadowWorker("jev-gate-test")
    seen: list[int] = []
    for i in range(3):
        worker.submit(partial(seen.append, i))
    worker.submit(lambda: 1 / 0)  # a failing task does not kill the worker
    worker.submit(lambda: seen.append(9))
    assert worker.drain(2.0) == 0
    assert seen == [0, 1, 2, 9]
    worker.submit(lambda: seen.append(99))  # closed: ignored
    assert seen == [0, 1, 2, 9]

    slow = ShadowWorker("jev-gate-slow")
    slow.submit(lambda: threading.Event().wait(0.5))
    slow.submit(lambda: None)
    started = time.monotonic()
    assert slow.drain(0.05) >= 1
    assert time.monotonic() - started < 0.4
    assert slow._thread is not None and slow._thread.daemon
    assert ShadowWorker("never-started").drain(1.0) == 0


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (None, None),
        ({"id": "b1"}, None),  # DB predates the column: off
        ({"id": "b1", "jev_group_gate": "off"}, None),
        ({"id": "b1", "jev_group_gate": "bogus"}, None),
        ({"id": "b1", "jev_group_gate": "shadow", "focus_category": "Raw"}, "shadow"),
        ({"id": "b1", "jev_group_gate": "enforce"}, "enforce"),
    ],
)
def test_build_group_gate_mode_from_row(
    monkeypatch: pytest.MonkeyPatch, row: dict[str, Any] | None, expected: str | None
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-key")
    monkeypatch.setattr(gate_factory, "current_brand_id", lambda: "b1")
    monkeypatch.setattr(brands_db, "get", lambda _b: row)
    gate = build_group_gate()
    assert (gate.mode if gate else None) == expected
    if gate and row and row.get("focus_category"):
        assert gate.brand_focus == "Raw"


def test_build_group_gate_disabled_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-key")
    monkeypatch.setattr(gate_factory, "current_brand_id", lambda: "b1")
    monkeypatch.setattr(brands_db, "get", lambda _b: {"jev_group_gate": "shadow"})
    assert build_group_gate(dry_run=True) is None

    def boom(_b: str) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(brands_db, "get", boom)
    assert build_group_gate() is None
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert build_group_gate() is None


def test_final_outcome_is_the_scouts_real_final_action() -> None:
    final = FinalOutcomes()
    final.note("p", outcomes.OUTCOME_SKIPPED_LOW_SCORE)  # the search copy
    final.note("p", outcomes.OUTCOME_JOINED)  # the pending copy was joined
    final.note("p", outcomes.OUTCOME_SKIPPED_CAP)  # never downgrades a join
    assert final.get("p") == "joined"
    final.note("q", outcomes.OUTCOME_SKIPPED_RANK_CUT)
    final.note("q", outcomes.OUTCOME_SKIPPED_CAP)
    assert final.get("q") == "skipped_cap"
    final.note("q", outcomes.OUTCOME_SKIPPED_BY_GATE)
    assert final.get("q") == "skipped_by_gate"
    final.note("", outcomes.OUTCOME_JOINED)
    assert final.get("") is None
