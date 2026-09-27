"""The seam between fb-group-scout and a group gate (no DB, no HTTP).

The scout only ever talks to a ``GroupGate``: ``screen`` a pool of cards
before it starts accepting/rejecting them, ``record``/``join_result`` as each
card meets its real fate, ``close`` once at the end of the run. The
disabled gate is ``NullGroupGate``; the Jev one lives in ``scout_gate``.

Contract: ``screen`` returns the SAME list object untouched unless the gate
is enforcing, where it returns a new list without the gate's skips. A gate
never adds, reorders or mutates a card, and no method ever raises.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Protocol, TypeVar

from lib.decisions import outcomes
from lib.observability import get_logger

log = get_logger(__name__)

G = TypeVar("G", bound=Mapping[str, object])


class GroupGate(Protocol):
    """What the scout needs from a gate (real or null)."""

    def screen(self, groups: list[G]) -> list[G]: ...
    def record(self, groups: Iterable[Mapping[str, object]], outcome: str) -> None: ...
    def join_result(self, group: Mapping[str, object], status: str) -> None: ...
    def close(self, timeout_s: float | None = None) -> None: ...


class NullGroupGate:
    """The disabled gate: every call is a no-op and ``screen`` is identity."""

    def screen(self, groups: list[G]) -> list[G]:
        return groups

    def record(self, groups: Iterable[Mapping[str, object]], outcome: str) -> None:
        return None

    def join_result(self, group: Mapping[str, object], status: str) -> None:
        return None

    def close(self, timeout_s: float | None = None) -> None:
        return None


def item_key(group: Mapping[str, object]) -> str:
    """The key a group's decision is filed under: its URL, the way the scout
    itself dedupes groups (lower-cased), without a trailing slash."""
    return str(group.get("url") or "").strip().rstrip("/").lower()


def outcome_for_join(group: Mapping[str, object], status: str) -> str:
    """Map one ``try_join`` result (or ``"error"``) to an outcome value.

    Mirrors ``send_join_requests``: a click on a private group is a request,
    on a public one an immediate join.
    """
    if status.startswith("clicked"):
        if group.get("privacy") == "private":
            return outcomes.OUTCOME_JOIN_REQUESTED
        return outcomes.OUTCOME_JOINED
    if status == "already_joined":
        return outcomes.OUTCOME_ALREADY_MEMBER
    if status == "already_pending":
        return outcomes.OUTCOME_ALREADY_PENDING
    return outcomes.OUTCOME_JOIN_FAILED


def screen_ranked(gate: GroupGate, groups: list[G], rank: Callable[[G], float]) -> list[G]:
    """Screen ``groups`` best-ranked first (so the per-run call cap is spent
    on the likeliest joins) but hand back the scout's own order.

    Returns ``groups`` itself whenever the gate drops nothing (always, in
    shadow). Never raises.
    """
    try:
        ranked = sorted(groups, key=rank, reverse=True)
        kept = gate.screen(ranked)
        if len(kept) == len(ranked):
            return groups
        kept_ids = {id(g) for g in kept}
        return [g for g in groups if id(g) in kept_ids]
    except Exception as exc:
        log.warning("jev_gate_error", stage="screen_ranked", error_type=type(exc).__name__)
        return groups


def dropped(before: Iterable[G], after: Iterable[G]) -> list[G]:
    """The cards in ``before`` that a scout step did not keep (by identity)."""
    kept_ids = {id(g) for g in after}
    return [g for g in before if id(g) not in kept_ids]
