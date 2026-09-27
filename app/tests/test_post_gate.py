"""Unit tests for `lib.decisions.post_gate` and `lib.decisions.engager_gate`.

`decide` is always a fake -- no network. The engager-gate tests replace the
`decisions_db` functions with in-memory recorders, so no database either.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from lib import draft_helper
from lib.decisions import decisions_db, engager_gate, jev_client, post_gate
from lib.decisions.engager_gate import JevPostGate, build_post_gate, outcome_for
from lib.decisions.jev_types import (
    ChoiceAnswer,
    JevResult,
    JsonDict,
    NoulAnswer,
    Question,
)
from lib.decisions.modes import GateMode, parse_mode
from lib.engagement.post import Post


def _result(relevant: float, value: str, value_conf: float, unsafe: float) -> JevResult:
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


def _decider(result: JevResult | None, calls: list[JsonDict] | None = None) -> post_gate.Decide:
    def _decide(state: str | JsonDict, questions: Mapping[str, Question]) -> JevResult | None:
        assert set(questions) == {"relevant", "value", "unsafe"}
        if calls is not None and isinstance(state, dict):
            calls.append(state)
        return result

    return _decide


_KEEP = _result(0.9, "answer_question", 0.9, 0.05)


# --- post_gate: modes + thresholds ---------------------------------------------


def test_off_mode_never_calls_jev() -> None:
    calls: list[JsonDict] = []
    verdict = post_gate.evaluate_post(
        "instagram", "dog food", "text", mode="off", decide=_decider(_KEEP, calls)
    )
    assert verdict is None
    assert calls == []


def test_jev_failure_yields_no_verdict() -> None:
    assert (
        post_gate.evaluate_post("instagram", "f", "t", mode="shadow", decide=_decider(None)) is None
    )


def test_state_carries_platform_focus_and_text() -> None:
    calls: list[JsonDict] = []
    post_gate.evaluate_post(
        "facebook",
        "dog food",
        "my dog?",
        mode="shadow",
        source_name="Grp",
        decide=_decider(_KEEP, calls),
    )
    assert calls == [
        {
            "platform": "facebook",
            "brand_focus": "dog food",
            "posted_in": "Grp",
            "post_text": "my dog?",
        }
    ]


@pytest.mark.parametrize(
    ("result", "reasons"),
    [
        (_KEEP, ()),
        (_result(0.9, "share_experience", 0.9, 0.8), ("unsafe",)),
        (_result(0.9, "share_experience", 0.9, 0.79), ()),
        (_result(0.2, "share_experience", 0.9, 0.0), ("not_relevant",)),
        (_result(0.21, "share_experience", 0.9, 0.0), ()),
        (_result(0.9, "nothing_to_add", 0.8, 0.0), ("nothing_to_add",)),
        (_result(0.9, "nothing_to_add", 0.79, 0.0), ()),
        (_result(0.1, "nothing_to_add", 0.95, 0.9), ("unsafe", "not_relevant", "nothing_to_add")),
    ],
)
def test_enforce_thresholds(result: JevResult, reasons: tuple[str, ...]) -> None:
    verdict = post_gate.evaluate_post(
        "instagram", "f", "t", mode="enforce", decide=_decider(result)
    )
    assert verdict is not None
    assert verdict.skip_reasons == reasons
    assert verdict.would_skip is bool(reasons)
    assert verdict.should_skip is bool(reasons)


def test_shadow_computes_would_skip_but_never_should_skip() -> None:
    skip = _result(0.0, "nothing_to_add", 1.0, 1.0)
    verdict = post_gate.evaluate_post("instagram", "f", "t", mode="shadow", decide=_decider(skip))
    assert verdict is not None
    assert verdict.would_skip is True
    assert verdict.should_skip is False


def test_default_decide_resolves_jev_client_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jev_client, "decide", _decider(_KEEP))
    verdict = post_gate.evaluate_post("instagram", "f", "t", mode="shadow")
    assert verdict is not None and verdict.value == "answer_question"


def test_parse_mode_fails_safe() -> None:
    assert parse_mode("SHADOW") == "shadow"
    assert parse_mode("enforce") == "enforce"
    assert parse_mode("bogus") == "off"
    assert parse_mode(None) == "off"


# --- engager_gate ------------------------------------------------------------


class _Store:
    """In-memory stand-in for the three decisions_db functions the gate uses."""

    def __init__(self, existing: bool = False) -> None:
        self.existing = existing
        self.decisions: list[decisions_db.DecisionRecord] = []
        self.outcomes: list[tuple[str, str]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(decisions_db, "has_decision", lambda *_a: self.existing)
        monkeypatch.setattr(decisions_db, "record_decision", self._record)
        monkeypatch.setattr(decisions_db, "record_outcome", self._outcome)

    def _record(self, record: decisions_db.DecisionRecord) -> bool:
        self.decisions.append(record)
        return True

    def _outcome(self, key: str, outcome: str, **_kw: Any) -> bool:
        self.outcomes.append((key, outcome))
        return True


def _post(pid: str = "p1") -> Post:
    return Post(platform="instagram", post_id=pid, post_url=f"https://x/p/{pid}", text="Any tips?")


def _gate(mode: GateMode, max_calls: int = 60) -> JevPostGate:
    return JevPostGate(
        brand_id="b",
        flow="ig-engager",
        platform="instagram",
        mode=mode,
        brand_focus="dog food",
        max_calls=max_calls,
    )


def test_shadow_gate_records_and_never_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    store.install(monkeypatch)
    monkeypatch.setattr(jev_client, "decide", _decider(_result(0.0, "nothing_to_add", 1.0, 1.0)))
    gate = _gate("shadow")

    assert gate.before_draft(_post()) is False
    assert len(store.decisions) == 1
    rec = store.decisions[0]
    assert (rec.mode, rec.would_skip, rec.item_key) == ("shadow", True, "https://x/p/p1")
    assert rec.cost_usd == 0.0001 and rec.latency_ms == 12
    assert store.outcomes == []  # shadow never stamps skipped_by_gate


def test_enforce_gate_skips_and_stamps_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    store.install(monkeypatch)
    monkeypatch.setattr(jev_client, "decide", _decider(_result(0.0, "nothing_to_add", 1.0, 1.0)))
    assert _gate("enforce").before_draft(_post()) is True
    assert store.outcomes == [("https://x/p/p1", "skipped_by_gate")]


def test_gate_skips_jev_for_already_decided_post(monkeypatch: pytest.MonkeyPatch) -> None:
    _Store(existing=True).install(monkeypatch)
    calls: list[JsonDict] = []
    monkeypatch.setattr(jev_client, "decide", _decider(_KEEP, calls))
    assert _gate("enforce").before_draft(_post()) is False
    assert calls == []


def test_gate_respects_run_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _Store().install(monkeypatch)
    calls: list[JsonDict] = []
    monkeypatch.setattr(jev_client, "decide", _decider(_KEEP, calls))
    gate = _gate("shadow", max_calls=2)
    for i in range(5):
        gate.before_draft(_post(f"p{i}"))
    assert len(calls) == 2 and gate.calls == 2


def test_gate_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> bool:
        raise RuntimeError("db down")

    monkeypatch.setattr(decisions_db, "has_decision", _boom)
    monkeypatch.setattr(decisions_db, "record_outcome", _boom)
    gate = _gate("enforce")
    assert gate.before_draft(_post()) is False
    gate.after_draft(_post(), drafted=True, reason=None)  # must not raise


def test_after_draft_maps_drafter_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    store.install(monkeypatch)
    gate = _gate("shadow")
    gate.after_draft(_post(), drafted=True, reason=None)
    gate.after_draft(_post(), drafted=False, reason=draft_helper.AGENT_DECLINED)
    gate.after_draft(_post(), drafted=False, reason=draft_helper.DRAFT_FAILED)
    assert [o for _k, o in store.outcomes] == ["engaged", "declined", "drafter_error"]


def test_outcome_mapping_pins_draft_helper_constants() -> None:
    assert engager_gate.DRAFTER_ERROR_REASONS == {
        draft_helper.DRAFT_FAILED,
        draft_helper.DRAFT_BLANK,
    }
    assert engager_gate.VOICE_FAILED_REASON == draft_helper.VOICE_FAILED
    assert outcome_for(drafted=False, reason=draft_helper.VOICE_FAILED) == "engaged"
    assert outcome_for(drafted=False, reason=None) == "declined"


# --- build_post_gate ---------------------------------------------------------


def test_build_gate_none_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(jev_client.API_KEY_ENV, raising=False)
    monkeypatch.setattr(jev_client, "_missing_key_warned", True)
    assert build_post_gate("instagram", "ig-engager") is None


@pytest.mark.parametrize(
    ("row", "platform", "expected"),
    [
        ({"jev_post_gate_ig": "shadow", "focus_category": "Dog Food"}, "instagram", "shadow"),
        ({"jev_post_gate_fb": "enforce", "niche": "dogs"}, "facebook", "enforce"),
        ({"jev_post_gate_ig": "off"}, "instagram", None),
        (None, "instagram", None),
        ({"jev_post_gate_ig": "shadow"}, "tiktok", None),
    ],
)
def test_build_gate_reads_mode_from_brand_row(
    monkeypatch: pytest.MonkeyPatch, row: dict[str, str] | None, platform: str, expected: str | None
) -> None:
    monkeypatch.setenv(jev_client.API_KEY_ENV, "test-not-a-real-key")
    monkeypatch.setattr(engager_gate, "current_brand_id", lambda: "acme")
    monkeypatch.setattr(engager_gate.brands_db, "get", lambda _bid: row)
    gate = build_post_gate(platform, "flow")
    assert (gate.mode if gate else None) == expected
    if gate is not None and row is not None:
        assert gate.brand_focus == (row.get("focus_category") or row.get("niche"))


def test_build_gate_survives_db_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(jev_client.API_KEY_ENV, "test-not-a-real-key")

    def _boom(_bid: str) -> None:
        raise RuntimeError("no db")

    monkeypatch.setattr(engager_gate.brands_db, "get", _boom)
    assert build_post_gate("instagram", "ig-engager") is None
