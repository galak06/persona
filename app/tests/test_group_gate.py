"""Unit tests for `lib.decisions.group_gate` (the FB group match questions).

No network: every Jev call goes through a fake `decide`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from lib.decisions import group_gate, jev_client
from lib.decisions.group_gate import (
    Q_ACTIVE,
    Q_CAN_COMMENT,
    Q_MATCH,
    Q_NORTH_AMERICA,
    build_questions,
    evaluate_group,
    group_state,
    skip_reasons,
)
from lib.decisions.jev_types import (
    Answer,
    ChoiceAnswer,
    ChoiceQuestion,
    JevResult,
    JsonDict,
    NoulAnswer,
    NoulQuestion,
    Question,
    ScoreAnswer,
    ScoreQuestion,
)

CARD: dict[str, Any] = {
    "url": "https://www.facebook.com/groups/homemadedogfood",
    "name": "Homemade Dog Food Recipes USA",
    "privacy": "private",
    "member_text": "12K members",
    "member_count": 12_000,
    "post_frequency": "10 posts a day",
    "description": "Share your homemade dog food recipes.",
    "score": 70,
    "found_via_query": "homemade dog food",
}


def _answers(
    match: str = "match",
    match_confidence: float = 0.9,
    north_america: float = 0.9,
    can_comment: float = 0.9,
    active: float = 2.0,
) -> dict[str, Answer]:
    return {
        Q_MATCH: ChoiceAnswer(choice=match, confidence=match_confidence),
        Q_NORTH_AMERICA: NoulAnswer(noul=north_america),
        Q_CAN_COMMENT: NoulAnswer(noul=can_comment),
        Q_ACTIVE: ScoreAnswer(score=active, confidence=0.7),
    }


class FakeDecide:
    def __init__(self, answers: dict[str, Answer] | None) -> None:
        self.answers = answers
        self.calls: list[tuple[str | JsonDict, Mapping[str, Question]]] = []

    def __call__(
        self, state: str | JsonDict, questions: Mapping[str, Question]
    ) -> JevResult | None:
        self.calls.append((state, questions))
        if self.answers is None:
            return None
        return JevResult(
            answers=self.answers,
            raw_answers={k: {"x": 1} for k in self.answers},
            model="typesafe/jev-1.13",
            latency_ms=12,
            cost_usd=0.0004,
        )


def test_questions_are_typed_as_specified() -> None:
    qs = build_questions("homemade dog food")
    assert set(qs) == {Q_MATCH, Q_NORTH_AMERICA, Q_CAN_COMMENT, Q_ACTIVE}
    match = qs[Q_MATCH]
    assert isinstance(match, ChoiceQuestion)
    assert set(match.criteria) == {"match", "adjacent", "off_topic"}
    assert "homemade dog food" in match.criteria["match"]
    assert isinstance(qs[Q_NORTH_AMERICA], NoulQuestion)
    assert isinstance(qs[Q_CAN_COMMENT], NoulQuestion)
    active = qs[Q_ACTIVE]
    assert isinstance(active, ScoreQuestion) and len(active.criteria) == 4


def test_empty_focus_falls_back() -> None:
    match = build_questions("  ")[Q_MATCH]
    assert isinstance(match, ChoiceQuestion)
    assert "dog food and recipes" in match.instructions


def test_state_carries_only_what_the_scout_has() -> None:
    state = group_state("dog food", CARD)
    assert state == {
        "brand_focus": "dog food",
        "url": CARD["url"],
        "name": CARD["name"],
        "privacy": "private",
        "member_text": "12K members",
        "post_frequency": "10 posts a day",
        "member_count": 12_000,
        "description": CARD["description"],
    }
    # Scout-internal fields never leak into the classifier's state.
    assert "score" not in state and "found_via_query" not in state


def test_state_drops_unknown_member_count_and_truncates_description() -> None:
    card = {**CARD, "member_count": 0, "description": "x" * 5000}
    state = group_state("f", card)
    assert "member_count" not in state
    assert len(str(state["description"])) == group_gate.MAX_DESCRIPTION_CHARS
    assert group_state("f", {})["name"] == ""


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, ()),
        ({"match": "adjacent", "off_topic": 0.99}, ()),
        ({"match": "off_topic", "off_topic": 0.8}, ("off_topic",)),
        ({"match": "off_topic", "off_topic": 0.79}, ()),
        ({"north_america": 0.2}, ("not_north_america",)),
        ({"north_america": 0.21}, ()),
        ({"can_comment": 0.2}, ("members_cannot_comment",)),
        ({"can_comment": 0.21}, ()),
        ({"active": 0.74}, ("inactive",)),
        ({"active": 0.75}, ()),
        (
            {"match": "off_topic", "north_america": 0.0, "can_comment": 0.0, "active": 0.0},
            ("off_topic", "not_north_america", "members_cannot_comment", "inactive"),
        ),
    ],
)
def test_thresholds(kwargs: dict[str, Any], expected: tuple[str, ...]) -> None:
    args: dict[str, Any] = {
        "match": "match",
        "off_topic": 0.9,
        "north_america": 0.9,
        "can_comment": 0.9,
        "active": 2.0,
        **kwargs,
    }
    assert skip_reasons(**args) == expected


def test_off_mode_never_calls_jev() -> None:
    fake = FakeDecide(_answers())
    assert evaluate_group("f", CARD, mode="off", decide=fake) is None
    assert fake.calls == []


def test_one_request_carries_all_four_questions() -> None:
    fake = FakeDecide(_answers())
    verdict = evaluate_group("dog food", CARD, mode="shadow", decide=fake)
    assert verdict is not None and not verdict.would_skip
    assert len(fake.calls) == 1
    state, questions = fake.calls[0]
    assert isinstance(state, dict) and state["name"] == CARD["name"]
    assert set(questions) == {Q_MATCH, Q_NORTH_AMERICA, Q_CAN_COMMENT, Q_ACTIVE}
    assert set(verdict.questions) == set(questions)
    assert verdict.result.cost_usd == 0.0004


def test_shadow_never_acts_but_enforce_does() -> None:
    skip = _answers(match="off_topic", match_confidence=1.0)
    shadow = evaluate_group("f", CARD, mode="shadow", decide=FakeDecide(skip))
    enforce = evaluate_group("f", CARD, mode="enforce", decide=FakeDecide(skip))
    assert shadow is not None and shadow.would_skip and not shadow.should_skip
    assert enforce is not None and enforce.would_skip and enforce.should_skip
    assert enforce.skip_reasons == ("off_topic",)


def test_jev_failure_is_none() -> None:
    assert evaluate_group("f", CARD, mode="enforce", decide=FakeDecide(None)) is None


def test_wrongly_typed_answers_are_none() -> None:
    bad = _answers()
    bad[Q_ACTIVE] = NoulAnswer(noul=0.5)
    assert evaluate_group("f", CARD, mode="shadow", decide=FakeDecide(bad)) is None


def test_default_decide_is_the_patchable_client(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDecide(_answers(active=0.1))
    monkeypatch.setattr(jev_client, "decide", fake)
    verdict = evaluate_group("f", CARD, mode="shadow")
    assert verdict is not None and verdict.skip_reasons == ("inactive",)
    assert len(fake.calls) == 1


def test_off_topic_threshold_uses_the_label_probability() -> None:
    """Not ChoiceAnswer.confidence: that may be a near-constant 1."""
    answers = _answers()
    answers[Q_MATCH] = ChoiceAnswer(
        choice="off_topic", confidence=1.0, probabilities={"off_topic": 0.55, "adjacent": 0.45}
    )
    verdict = evaluate_group("f", CARD, mode="enforce", decide=FakeDecide(answers))
    assert verdict is not None and verdict.off_topic == 0.55 and not verdict.would_skip
    answers[Q_MATCH] = ChoiceAnswer(
        choice="off_topic", confidence=0.1, probabilities={"off_topic": 0.9}
    )
    verdict = evaluate_group("f", CARD, mode="enforce", decide=FakeDecide(answers))
    assert verdict is not None and verdict.skip_reasons == ("off_topic",)
    assert group_gate.off_topic_probability(ChoiceAnswer(choice="match", confidence=0.9)) == 0.0
