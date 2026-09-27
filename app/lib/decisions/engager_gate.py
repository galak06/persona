"""The Jev post gate as an engagement-pipeline collaborator.

Satisfies ``lib.engagement.collaborators.DecisionGate`` structurally. The
pipeline calls ``before_draft`` right before the drafter would run (after
the comment gate, quota and claim checks, so a post that could never be
commented costs no Jev call) and ``after_draft`` once the drafter decided.

Contract with the pipeline: ``before_draft`` returns True ONLY in
``enforce`` mode for a post the gate would skip. In ``shadow`` it always
returns False -- the verdict is logged and stored, nothing else. Both
methods never raise.
"""

from __future__ import annotations

from typing import Final, Protocol

from lib import brands_db
from lib.brand_context import current_brand_id
from lib.decisions import decisions_db
from lib.decisions.decisions_db import DecisionRecord
from lib.decisions.jev_client import api_key_configured, warn_missing_key_once
from lib.decisions.modes import MODE_OFF, GateMode, parse_mode
from lib.decisions.post_gate import PostGateVerdict, evaluate_post
from lib.observability import get_logger

log = get_logger(__name__)


class Post(Protocol):
    """The slice of `lib.engagement.post.Post` the gate reads.

    Structural so this package never imports `lib.engagement` (which keeps
    `lib/decisions/` independently mypy-checkable in CI).
    """

    @property
    def post_id(self) -> str: ...
    @property
    def post_url(self) -> str: ...
    @property
    def text(self) -> str: ...
    @property
    def source_name(self) -> str | None: ...


# One Jev request per post; this caps a run's spend no matter how many
# candidates a scan turns up.
MAX_CALLS_PER_RUN: Final = 60

MODE_COLUMNS: Final[dict[str, str]] = {
    "instagram": "jev_post_gate_ig",
    "facebook": "jev_post_gate_fb",
}

# Mirrors lib.draft_helper's DRAFT_FAILED / DRAFT_BLANK (pinned by a test).
# Those are upstream failures, not an editorial decision. Everything else
# that yields no text -- agent_declined, or a drafter without
# `last_outcome` -- is a decline. voice_validation_failed only happens after
# the drafter chose to engage, so it counts as engaged.
DRAFTER_ERROR_REASONS: Final = frozenset({"draft_failed", "draft_blank"})
VOICE_FAILED_REASON: Final = "voice_validation_failed"


def item_key(post: Post) -> str:
    """The stable key a post's decision and outcome are filed under."""
    return post.post_url or post.post_id


def outcome_for(*, drafted: bool, reason: str | None) -> str:
    """Map the drafter's result to a ``jev_decisions.outcome`` value."""
    if drafted or reason == VOICE_FAILED_REASON:
        return decisions_db.OUTCOME_ENGAGED
    if reason in DRAFTER_ERROR_REASONS:
        return decisions_db.OUTCOME_DRAFTER_ERROR
    return decisions_db.OUTCOME_DECLINED


class JevPostGate:
    """Per-run gate state: brand, mode, focus and the call budget."""

    def __init__(
        self,
        *,
        brand_id: str,
        flow: str,
        platform: str,
        mode: GateMode,
        brand_focus: str,
        max_calls: int = MAX_CALLS_PER_RUN,
    ) -> None:
        self.brand_id = brand_id
        self.flow = flow
        self.platform = platform
        self.mode = mode
        self.brand_focus = brand_focus
        self.max_calls = max_calls
        self.calls = 0
        self._cap_logged = False

    def before_draft(self, post: Post) -> bool:
        """Evaluate + log one post; True only when enforce says skip."""
        try:
            return self._before_draft(post)
        except Exception as exc:  # a classifier bug must never stop a comment
            log.warning("jev_gate_error", stage="before_draft", error_type=type(exc).__name__)
            return False

    def after_draft(self, post: Post, *, drafted: bool, reason: str | None) -> None:
        """Stamp what the drafter did against this post's decision row."""
        try:
            decisions_db.record_outcome(
                item_key(post),
                outcome_for(drafted=drafted, reason=reason),
                brand_id=self.brand_id,
                platform=self.platform,
            )
        except Exception as exc:
            log.warning("jev_gate_error", stage="after_draft", error_type=type(exc).__name__)

    def _before_draft(self, post: Post) -> bool:
        if self.mode == MODE_OFF:
            return False
        key = item_key(post)
        if decisions_db.has_decision(self.brand_id, self.platform, key):
            return False
        if self.calls >= self.max_calls:
            if not self._cap_logged:
                self._cap_logged = True
                log.info("jev_run_cap_reached", platform=self.platform, max_calls=self.max_calls)
            return False
        self.calls += 1
        verdict = evaluate_post(
            self.platform,
            self.brand_focus,
            post.text,
            mode=self.mode,
            source_name=post.source_name,
        )
        if verdict is None:
            return False
        self._record(key, verdict)
        if verdict.should_skip:
            decisions_db.record_outcome(
                key,
                decisions_db.OUTCOME_SKIPPED_BY_GATE,
                brand_id=self.brand_id,
                platform=self.platform,
            )
        return verdict.should_skip

    def _record(self, key: str, verdict: PostGateVerdict) -> None:
        log.info(
            "jev_post_gate_decision",
            platform=self.platform,
            mode=self.mode,
            item_key=key,
            relevant=round(verdict.relevant, 3),
            value=verdict.value,
            unsafe=round(verdict.unsafe, 3),
            would_skip=verdict.would_skip,
            skip_reasons=list(verdict.skip_reasons),
            latency_ms=verdict.result.latency_ms,
            cost_usd=verdict.result.cost_usd,
        )
        decisions_db.record_decision(
            DecisionRecord(
                brand_id=self.brand_id,
                flow=self.flow,
                platform=self.platform,
                item_key=key,
                questions=verdict.questions,
                answers=verdict.result.raw_answers,
                mode=self.mode,
                would_skip=verdict.would_skip,
                latency_ms=verdict.result.latency_ms,
                cost_usd=verdict.result.cost_usd,
            )
        )


def _brand_focus(row: dict[str, object]) -> str:
    focus = str(row.get("focus_category") or "").strip()
    return focus or str(row.get("niche") or "").strip()


def build_post_gate(platform: str, flow: str) -> JevPostGate | None:
    """The gate for this process's brand, or None when it should not run.

    None when: the platform has no mode column, ``OPENROUTER_API_KEY`` is
    unset (warned once), the brand row cannot be read, or its mode is off.
    Never raises -- an engager must start whether or not Jev is available.
    """
    column = MODE_COLUMNS.get(platform)
    if column is None:
        return None
    if not api_key_configured():
        warn_missing_key_once()
        return None
    brand_id = current_brand_id()
    try:
        row = brands_db.get(brand_id)
    except Exception as exc:
        log.warning("jev_gate_brand_lookup_failed", error_type=type(exc).__name__)
        return None
    if row is None:
        log.info("jev_gate_brand_unregistered", brand_id=brand_id)
        return None
    mode = parse_mode(row.get(column))
    if mode == MODE_OFF:
        return None
    log.info("jev_gate_enabled", brand_id=brand_id, platform=platform, mode=mode)
    return JevPostGate(
        brand_id=brand_id, flow=flow, platform=platform, mode=mode, brand_focus=_brand_focus(row)
    )
