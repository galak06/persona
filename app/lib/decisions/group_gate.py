"""The FB group match gate: four Jev questions asked before the scout joins.

For a candidate group the scout found (a search-result card, or a queued
card from a previous run), Jev is asked (in ONE request):

* ``match``               (choice) -- ``match`` (a dog-owner community on the
  brand's focus topic), ``adjacent`` (a general dog community) or
  ``off_topic``.
* ``north_america``       (noul)   -- is the audience mainly US/Canada?
* ``members_can_comment`` (noul)   -- can an ordinary new member comment,
  as opposed to admin-only posting or buy/sell/rehoming-only groups?
* ``active``              (score 0-3) -- dead/spam .. very active discussion.

The state is only what the scout already scraped: name, description,
privacy, member count and the card's post-frequency line. Search cards
carry no rules text or post snippets, so those fields are absent rather
than invented.

``would_skip`` is computed identically in every mode; only ``enforce`` acts
on it (``GroupGateVerdict.should_skip``). In ``shadow`` the verdict is a log
line and a DB row, nothing more.
"""

from __future__ import annotations

from collections.abc import Mapping
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
    ScoreAnswer,
    ScoreQuestion,
    questions_to_json,
)
from lib.decisions.modes import MODE_ENFORCE, MODE_OFF, GateMode
from lib.decisions.post_gate import Decide

# Enforce thresholds. Deliberately conservative: shadow data decides whether
# they tighten before enforce is ever switched on.
# Thresholded on the off_topic PROBABILITY (what the Decisions page shows),
# not ChoiceAnswer.confidence, which Jev may report as a near-constant.
OFF_TOPIC_PROBABILITY: Final = 0.8
NORTH_AMERICA_SKIP_AT_OR_BELOW: Final = 0.2
CAN_COMMENT_SKIP_AT_OR_BELOW: Final = 0.2
ACTIVE_SKIP_BELOW: Final = 0.75

Q_MATCH: Final = "match"
Q_NORTH_AMERICA: Final = "north_america"
Q_CAN_COMMENT: Final = "members_can_comment"
Q_ACTIVE: Final = "active"
MATCH_MATCH: Final = "match"
MATCH_ADJACENT: Final = "adjacent"
MATCH_OFF_TOPIC: Final = "off_topic"

# Card text beyond this is noise for a four-question classification.
MAX_DESCRIPTION_CHARS: Final = 2000
_CARD_FIELDS: Final = ("url", "name", "privacy", "member_text", "post_frequency")


def build_questions(brand_focus: str) -> dict[str, Question]:
    """The four group-gate questions for one brand's focus topic."""
    focus = brand_focus.strip() or "dog food and recipes"
    return {
        Q_MATCH: ChoiceQuestion(
            instructions=(
                "This is a Facebook group a dog-food brand is considering joining to take "
                f"part in discussions about {focus}. How well does the group fit?"
            ),
            criteria={
                MATCH_MATCH: f"A community of dog owners where {focus} is a natural topic.",
                MATCH_ADJACENT: "A general dog-owner community (breed, local or pet chat).",
                MATCH_OFF_TOPIC: (
                    "Not a dog-owner discussion community: marketplace, rescue listings, "
                    "travel, a business page, or an unrelated topic."
                ),
            },
        ),
        Q_NORTH_AMERICA: NoulQuestion(
            instructions="Is this group's audience mainly in the United States or Canada?"
        ),
        Q_CAN_COMMENT: NoulQuestion(
            instructions=(
                "Can an ordinary new member comment on posts in this group? No if only admins "
                "post, or if the group is only for buying, selling or rehoming animals."
            )
        ),
        Q_ACTIVE: ScoreQuestion(
            instructions="How active is real discussion in this group?",
            criteria=(
                "Dead, or only spam and ads.",
                "Occasional posts.",
                "Regular discussion.",
                "Very active, real discussion every day.",
            ),
        ),
    }


@dataclass(frozen=True)
class GroupGateVerdict:
    """One evaluated group: Jev's answers plus the gate's reading of them."""

    mode: GateMode
    match: str
    off_topic: float  # P(off_topic); falls back to confidence when absent
    north_america: float
    can_comment: float
    active: float
    would_skip: bool
    skip_reasons: tuple[str, ...]
    questions: JsonDict
    result: JevResult

    @property
    def should_skip(self) -> bool:
        """The ONLY field a caller may act on: True solely under ``enforce``."""
        return self.mode == MODE_ENFORCE and self.would_skip


def skip_reasons(
    match: str, off_topic: float, north_america: float, can_comment: float, active: float
) -> tuple[str, ...]:
    """Every enforce threshold this answer set trips (empty = keep the group)."""
    reasons: list[str] = []
    if match == MATCH_OFF_TOPIC and off_topic >= OFF_TOPIC_PROBABILITY:
        reasons.append("off_topic")
    if north_america <= NORTH_AMERICA_SKIP_AT_OR_BELOW:
        reasons.append("not_north_america")
    if can_comment <= CAN_COMMENT_SKIP_AT_OR_BELOW:
        reasons.append("members_cannot_comment")
    if active < ACTIVE_SKIP_BELOW:
        reasons.append("inactive")
    return tuple(reasons)


def off_topic_probability(match: ChoiceAnswer) -> float:
    """P(off_topic) as the Decisions page renders it: the label's probability,
    else the answer's confidence when it IS the chosen label, else 0."""
    prob = match.probabilities.get(MATCH_OFF_TOPIC)
    if prob is not None:
        return prob
    return match.confidence if match.choice == MATCH_OFF_TOPIC else 0.0


def group_state(brand_focus: str, group: Mapping[str, object]) -> JsonDict:
    """The Jev ``state`` for one scout card -- only fields the scout has."""
    state: JsonDict = {"brand_focus": brand_focus}
    for field in _CARD_FIELDS:
        state[field] = str(group.get(field) or "")
    member_count = group.get("member_count")
    if isinstance(member_count, int) and not isinstance(member_count, bool) and member_count > 0:
        state["member_count"] = member_count
    state["description"] = str(group.get("description") or "")[:MAX_DESCRIPTION_CHARS]
    return state


def evaluate_group(
    brand_focus: str,
    group: Mapping[str, object],
    *,
    mode: GateMode,
    decide: Decide | None = None,
) -> GroupGateVerdict | None:
    """Ask the four questions about one group; None when off or Jev failed.

    ``decide`` defaults to ``jev_client.decide``, resolved per call so a
    test can patch the module attribute. Never raises.
    """
    if mode == MODE_OFF:
        return None
    questions = build_questions(brand_focus)
    ask = decide if decide is not None else jev_client.decide
    result = ask(group_state(brand_focus, group), questions)
    if result is None:
        return None
    match = result.answers.get(Q_MATCH)
    north_america = result.answers.get(Q_NORTH_AMERICA)
    can_comment = result.answers.get(Q_CAN_COMMENT)
    active = result.answers.get(Q_ACTIVE)
    if not (
        isinstance(match, ChoiceAnswer)
        and isinstance(north_america, NoulAnswer)
        and isinstance(can_comment, NoulAnswer)
        and isinstance(active, ScoreAnswer)
    ):
        return None
    off_topic = off_topic_probability(match)
    reasons = skip_reasons(
        match.choice, off_topic, north_america.noul, can_comment.noul, active.score
    )
    return GroupGateVerdict(
        mode=mode,
        match=match.choice,
        off_topic=off_topic,
        north_america=north_america.noul,
        can_comment=can_comment.noul,
        active=active.score,
        would_skip=bool(reasons),
        skip_reasons=reasons,
        questions=questions_to_json(questions),
        result=result,
    )
