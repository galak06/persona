"""The ``jev_decisions.outcome`` vocabulary and the agreement rule.

An outcome is what the flow actually did with an item Jev was asked about:
the engagers' drafter (slice 1) or fb-group-scout (platform ``fb_group``).
Kept separate from the repository so the API and the gates share one list.
"""

from __future__ import annotations

from typing import Final

OUTCOME_ENGAGED: Final = "engaged"
OUTCOME_DECLINED: Final = "declined"
OUTCOME_DRAFTER_ERROR: Final = "drafter_error"
OUTCOME_SKIPPED_BY_GATE: Final = "skipped_by_gate"
# fb-group-scout outcomes (platform 'fb_group'), one per real scout branch.
OUTCOME_JOINED: Final = "joined"
OUTCOME_JOIN_REQUESTED: Final = "join_requested"
OUTCOME_ALREADY_MEMBER: Final = "already_member"
OUTCOME_ALREADY_PENDING: Final = "already_pending"
OUTCOME_JOIN_FAILED: Final = "join_failed"
OUTCOME_SKIPPED_LOW_SCORE: Final = "skipped_low_score"
OUTCOME_SKIPPED_ADMISSION_CLOSED: Final = "skipped_admission_closed"
OUTCOME_SKIPPED_RANK_CUT: Final = "skipped_rank_cut"
OUTCOME_SKIPPED_CAP: Final = "skipped_cap"
OUTCOMES: Final = frozenset(
    {
        OUTCOME_ENGAGED,
        OUTCOME_DECLINED,
        OUTCOME_DRAFTER_ERROR,
        OUTCOME_SKIPPED_BY_GATE,
        OUTCOME_JOINED,
        OUTCOME_JOIN_REQUESTED,
        OUTCOME_ALREADY_MEMBER,
        OUTCOME_ALREADY_PENDING,
        OUTCOME_JOIN_FAILED,
        OUTCOME_SKIPPED_LOW_SCORE,
        OUTCOME_SKIPPED_ADMISSION_CLOSED,
        OUTCOME_SKIPPED_RANK_CUT,
        OUTCOME_SKIPPED_CAP,
    }
)
# Agreement vocabulary: an outcome that is the flow's own editorial "keep"
# agrees with would_skip=False, an editorial "skip" with would_skip=True.
# Everything else is not a judgement about the item Jev was asked about, so
# it is not compared: drafter/join errors, already-a-member, the gate's own
# skip, and three scout outcomes --
#   skipped_cap              the day's join budget ran out;
#   skipped_rank_cut         relative to the day's pool (below the top 15),
#                            not a property of the group;
#   skipped_admission_closed admission friction, which none of the group
#                            gate's four questions asks about.
KEEP_OUTCOMES: Final = (OUTCOME_ENGAGED, OUTCOME_JOINED, OUTCOME_JOIN_REQUESTED)
SKIP_OUTCOMES: Final = (OUTCOME_DECLINED, OUTCOME_SKIPPED_LOW_SCORE)


def agrees(would_skip: bool, outcome: object) -> bool | None:
    """Did the flow's decision match Jev's? None when not comparable."""
    if outcome in SKIP_OUTCOMES:
        return would_skip
    if outcome in KEEP_OUTCOMES:
        return not would_skip
    return None
