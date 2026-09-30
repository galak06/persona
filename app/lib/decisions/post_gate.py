"""The post-relevance gate: three Jev questions asked before the drafter runs.

For a post that already passed the keyword ``score_relevance`` heuristic,
Jev is asked (in ONE request):

* ``relevant`` (noul)  -- is the post about the brand's focus topic?
* ``value``    (choice) -- could the persona ``answer_question``,
  ``share_experience``, or is there ``nothing_to_add``?
* ``unsafe``   (noul)  -- grief, a medical emergency, a conflict or a sales
  pitch, where any brand comment would be inappropriate.

``would_skip`` is computed identically in every mode; only ``enforce`` acts
on it (``PostGateVerdict.should_skip``). In ``shadow`` the verdict is a log
line and a DB row, nothing more.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from lib.decisions import jev_client
from lib.decisions.jev_types import (
    ChoiceAnswer,
    ChoiceQuestion,
    JevResult,
    JsonDict,
    NoulAnswer,
    NoulQuestion,
    Question,
    questions_to_json,
)
from lib.decisions.modes import MODE_ENFORCE, MODE_OFF, GateMode

# Enforce thresholds. Deliberately conservative: shadow data decides whether
# they tighten before enforce is ever switched on.
UNSAFE_SKIP_AT: Final = 0.8
RELEVANT_SKIP_AT_OR_BELOW: Final = 0.2
NOTHING_TO_ADD_CONFIDENCE: Final = 0.8

Q_RELEVANT: Final = "relevant"
Q_VALUE: Final = "value"
Q_UNSAFE: Final = "unsafe"
VALUE_NOTHING_TO_ADD: Final = "nothing_to_add"

Decide = Callable[[str | JsonDict, Mapping[str, Question]], JevResult | None]


def build_questions(brand_focus: str) -> dict[str, Question]:
    """The three post-gate questions for one brand's focus topic."""
    focus = brand_focus.strip() or "the brand's topic"
    return {
        Q_RELEVANT: NoulQuestion(
            instructions=(
                f"Is this social media post substantially about {focus}? "
                "Passing mentions or unrelated topics count as no."
            )
        ),
        Q_VALUE: ChoiceQuestion(
            instructions=(
                f"A friendly dog owner who knows {focus} well is reading this post. "
                "What, if anything, could they usefully add in a comment?"
            ),
            criteria={
                "answer_question": "The post asks a question they could genuinely answer.",
                "share_experience": "They could share a relevant first-hand experience.",
                VALUE_NOTHING_TO_ADD: "Any comment would be filler or off-topic.",
            },
        ),
        Q_UNSAFE: NoulQuestion(
            instructions=(
                "Would it be inappropriate for a brand account to comment here? Yes if the "
                "post is about grief or a pet's death, a medical emergency, an argument or "
                "conflict, or is itself a sales pitch or advertisement."
            )
        ),
    }


@dataclass(frozen=True)
class PostGateVerdict:
    """One evaluated post: Jev's answers plus the gate's reading of them."""

    mode: GateMode
    relevant: float
    value: str
    value_confidence: float
    unsafe: float
    would_skip: bool
    skip_reasons: tuple[str, ...]
    questions: JsonDict
    result: JevResult

    @property
    def should_skip(self) -> bool:
        """The ONLY field a caller may act on: True solely under ``enforce``."""
        return self.mode == MODE_ENFORCE and self.would_skip


def skip_reasons(
    relevant: float, value: str, value_confidence: float, unsafe: float
) -> tuple[str, ...]:
    """Every enforce threshold this answer set trips (empty = keep the post)."""
    reasons: list[str] = []
    if unsafe >= UNSAFE_SKIP_AT:
        reasons.append("unsafe")
    if relevant <= RELEVANT_SKIP_AT_OR_BELOW:
        reasons.append("not_relevant")
    if value == VALUE_NOTHING_TO_ADD and value_confidence >= NOTHING_TO_ADD_CONFIDENCE:
        reasons.append("nothing_to_add")
    return tuple(reasons)


def _state(platform: str, brand_focus: str, post_text: str, source_name: str | None) -> JsonDict:
    return {
        "platform": platform,
        "brand_focus": brand_focus,
        "posted_in": source_name or "",
        "post_text": post_text[: jev_client.MAX_STATE_CHARS],
    }


def evaluate_post(
    platform: str,
    brand_focus: str,
    post_text: str,
    *,
    mode: GateMode,
    source_name: str | None = None,
    decide: Decide | None = None,
) -> PostGateVerdict | None:
    """Ask the three questions about one post; None when off or Jev failed.

    ``decide`` defaults to ``jev_client.decide``, resolved per call so a
    test can patch the module attribute. Never raises (``decide`` never
    raises, and the reading below only touches already-validated answers).
    """
    if mode == MODE_OFF:
        return None
    questions = build_questions(brand_focus)
    ask = decide if decide is not None else jev_client.decide
    result = ask(_state(platform, brand_focus, post_text, source_name), questions)
    if result is None:
        return None
    relevant = result.answers.get(Q_RELEVANT)
    value = result.answers.get(Q_VALUE)
    unsafe = result.answers.get(Q_UNSAFE)
    if not (
        isinstance(relevant, NoulAnswer)
        and isinstance(value, ChoiceAnswer)
        and isinstance(unsafe, NoulAnswer)
    ):
        return None
    reasons = skip_reasons(relevant.noul, value.choice, value.confidence, unsafe.noul)
    return PostGateVerdict(
        mode=mode,
        relevant=relevant.noul,
        value=value.choice,
        value_confidence=value.confidence,
        unsafe=unsafe.noul,
        would_skip=bool(reasons),
        skip_reasons=reasons,
        questions=questions_to_json(questions),
        result=result,
    )
