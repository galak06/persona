"""Typed request/response shapes for the Jev decisions endpoint.

Jev (``typesafe/jev-1.13`` on OpenRouter) is a classifier, not a chat model:
each request carries a ``state`` and a dict of named ``questions``, and each
answer comes back typed by the question it answers. The three question
types and their verbatim wire shapes:

* ``choice`` -> ``{"choice": "...", "probabilities": {...}, "confidence": 1}``
* ``score``  -> ``{"score": 1.15, "legend": {...}, "probabilities": {...},
  "confidence": 0.77}``
* ``noul``   -> ``{"noul": 0.99}``

Parsing is strict about what matters and lenient about the rest: an answer
missing its defining field is a malformed response (``parse_answers``
returns None), while an absent ``probabilities``/``confidence`` just
defaults.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

JsonDict = dict[str, object]

MAX_SCORE_LEVELS = 10


@dataclass(frozen=True)
class ChoiceQuestion:
    """Pick exactly one label; ``criteria`` maps label -> description."""

    instructions: str
    criteria: Mapping[str, str]

    def to_json(self) -> JsonDict:
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": dict(self.criteria),
        }


@dataclass(frozen=True)
class ScoreQuestion:
    """Rate on an ordinal scale; ``criteria[i]`` describes level ``i``."""

    instructions: str
    criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 2 <= len(self.criteria) <= MAX_SCORE_LEVELS:
            raise ValueError(f"score questions take 2..{MAX_SCORE_LEVELS} levels")

    def to_json(self) -> JsonDict:
        return {"type": "score", "instructions": self.instructions, "criteria": list(self.criteria)}


@dataclass(frozen=True)
class NoulQuestion:
    """A yes/no question answered as a probability of "yes"."""

    instructions: str

    def to_json(self) -> JsonDict:
        return {"type": "noul", "instructions": self.instructions}


Question = ChoiceQuestion | ScoreQuestion | NoulQuestion


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass(frozen=True)
class NoulAnswer:
    noul: float


Answer = ChoiceAnswer | ScoreAnswer | NoulAnswer


@dataclass(frozen=True)
class JevResult:
    """One successful decisions call.

    ``raw_answers`` is the answers object exactly as received -- what gets
    persisted, so a later schema change never loses fields this module did
    not model.
    """

    answers: dict[str, Answer]
    raw_answers: JsonDict
    model: str
    latency_ms: int
    cost_usd: float | None = None


def questions_to_json(questions: Mapping[str, Question]) -> JsonDict:
    """The ``questions`` request field."""
    return {key: q.to_json() for key, q in questions.items()}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _probabilities(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for key, raw in value.items():
        number = _number(raw)
        if number is not None:
            out[str(key)] = number
    return out


def parse_answer(question: Question, raw: object) -> Answer | None:
    """Parse one answer against the question that produced it, or None."""
    if not isinstance(raw, dict):
        return None
    confidence = _number(raw.get("confidence")) or 0.0
    probabilities = _probabilities(raw.get("probabilities"))
    if isinstance(question, ChoiceQuestion):
        choice = raw.get("choice")
        if not isinstance(choice, str) or not choice:
            return None
        return ChoiceAnswer(choice=choice, probabilities=probabilities, confidence=confidence)
    if isinstance(question, ScoreQuestion):
        score = _number(raw.get("score"))
        if score is None:
            return None
        return ScoreAnswer(score=score, probabilities=probabilities, confidence=confidence)
    noul = _number(raw.get("noul"))
    return None if noul is None else NoulAnswer(noul=noul)


def parse_answers(questions: Mapping[str, Question], body: object) -> dict[str, Answer] | None:
    """Every asked question's answer, or None if any is missing/malformed.

    All-or-nothing on purpose: a partial answer set cannot drive the gate's
    thresholds, and a gate that silently treats a missing ``unsafe`` as 0
    would be worse than no gate.
    """
    if not isinstance(body, dict):
        return None
    answers = body.get("answers")
    if not isinstance(answers, dict):
        return None
    parsed: dict[str, Answer] = {}
    for key, question in questions.items():
        answer = parse_answer(question, answers.get(key))
        if answer is None:
            return None
        parsed[key] = answer
    return parsed


def usage_cost(body: object) -> float | None:
    """``usage.cost`` in USD when the response carries one."""
    if not isinstance(body, dict):
        return None
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    return _number(usage.get("cost"))
