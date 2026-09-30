"""Unit tests for `lib.decisions.post_gate` (modes, thresholds, state shape).

`decide` is always a fake -- no network. The engager collaborator is tested
in `test_jev_engager_gate.py`, the factory + run budget in
`test_jev_gate_factory.py`.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from lib.decisions import jev_client, post_gate
from lib.decisions.jev_types import (
    ChoiceAnswer,
    JevResult,
    JsonDict,
    NoulAnswer,
    Question,
)
from lib.decisions.modes import parse_mode


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
