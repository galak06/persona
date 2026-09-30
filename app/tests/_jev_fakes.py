"""Shared fakes for the Jev gate tests: canned results, fake `decide`, and an
in-memory stand-in for the three gate-path `decisions_db` functions.

Nothing here touches the network or a database.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping

import pytest

from lib.decisions import decisions_db, jev_client
from lib.decisions.jev_types import ChoiceAnswer, JevResult, JsonDict, NoulAnswer, Question
from lib.engagement.post import Post


def jev_result(relevant: float, value: str, value_conf: float, unsafe: float) -> JevResult:
    return JevResult(
        answers={
            "relevant": NoulAnswer(noul=relevant),
            "value": ChoiceAnswer(choice=value, confidence=value_conf),
            "unsafe": NoulAnswer(noul=unsafe),
        },
        raw_answers={"relevant": {"noul": relevant}},
        model="typesafe/jev-1.13",
        latency_ms=12,
        cost_usd=0.0001,
    )


KEEP = jev_result(0.9, "answer_question", 0.9, 0.05)
SKIP_ALL = jev_result(0.0, "nothing_to_add", 1.0, 1.0)


class FakeJev:
    """A `decide` double: fixed result, optional sleep, records every call."""

    def __init__(self, result: JevResult | None, *, sleep_s: float = 0.0) -> None:
        self.result = result
        self.sleep_s = sleep_s
        self.calls: list[str | JsonDict] = []
        self.threads: list[str] = []

    def __call__(self, state: str | JsonDict, _q: Mapping[str, Question]) -> JevResult | None:
        self.calls.append(state)
        self.threads.append(threading.current_thread().name)
        if self.sleep_s:
            time.sleep(self.sleep_s)
        return self.result

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeJev:
        monkeypatch.setattr(jev_client, "decide", self)
        return self


class FakeStore:
    """In-memory `record_decision` / `lookup_decision` / `record_outcome`."""

    def __init__(self, stored: dict[str, bool] | None = None) -> None:
        self.stored = dict(stored or {})
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
        for key, outcome in self.outcomes:
            out[key] = outcome
        return out


def make_post(pid: str = "p1") -> Post:
    return Post(platform="instagram", post_id=pid, post_url=f"https://x/p/{pid}", text="Tips?")


def join_gate_threads(timeout_s: float = 5.0) -> None:
    """Wait for any in-flight `jev-gate` worker, so it finishes while a test's
    fakes are still installed (a drained-with-timeout gate may leave one)."""
    for thread in threading.enumerate():
        if thread.name.startswith("jev-gate"):
            thread.join(timeout=timeout_s)
