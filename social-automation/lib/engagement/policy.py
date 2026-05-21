"""EngagementPolicy — single source of truth for OutboundEngagement thresholds.

Holds all scoring gates (candidate, comment, approval) and daily action
quotas (comment, like) in one frozen dataclass. Built once from
`config.json` at scanner startup, then read-only for the rest of the run.

Replaces three patterns that drifted across the FB and IG scanners:
    - `config["content_analysis"]["relevance_threshold"]` inline reads
    - `config["content_analysis"]["approval_threshold"]` inline reads
    - `ig_comment_threshold = 0.75` hardcoded in ig_scan

This is the one place to tune scanner behavior in production. Slice 1
of the OutboundEngagement refactor — adapter + pipeline come later.

Design notes:
    - Frozen dataclass → fail loud if anything tries to mutate at runtime.
    - `from_config` is the one entry point — never construct directly in
      production code, so behavior tuning always flows through config.json.
    - Logs construction at INFO so production runs surface the resolved
      thresholds in launchd output (debuggable from logs alone).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

_log = logging.getLogger(__name__)

# Defaults — match current production behavior (CLAUDE.md rate-limits + ig_scan
# hardcode). Anything missing from config.json falls back to these, so the
# policy is constructible from a minimal config.
_DEFAULT_IG_COMMENT_THRESHOLD: float = 0.75
_DEFAULT_FB_COMMENT_QUOTA: int = 5
_DEFAULT_IG_COMMENT_QUOTA: int = 10
_DEFAULT_FB_LIKE_QUOTA: int = 5  # Slice 4 activates FB inline likes; conservative starting cap.
_DEFAULT_IG_LIKE_QUOTA: int = 8


def _as_mapping(value: object) -> dict[str, object]:
    """Narrow `object` to a `dict[str, object]` for typed lookup. Empty on miss."""
    if isinstance(value, dict):
        # config.json is JSON — keys are always strings. Mypy can't know that
        # from the JSON loader signature, so we rebuild a typed view.
        return {str(k): v for k, v in value.items()}
    return {}


def _as_float(value: object, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


@dataclass(frozen=True)
class EngagementPolicy:
    """All thresholds and quotas for OutboundEngagement, immutable per run."""

    candidate_threshold: float
    """Gate to enter the candidate pool / make the like decision.
    Was `config["content_analysis"]["relevance_threshold"]`."""

    comment_threshold: float
    """Score required to queue a comment. IG: 0.75 (was hardcoded);
    FB: equals `candidate_threshold` today. Unification in a later slice."""

    approval_threshold: float
    """Below this score → `requires_approval=True` on the queue record."""

    daily_comment_quota: dict[str, int]
    """Max comments per platform per day. Keys: 'facebook', 'instagram'."""

    daily_like_quota: dict[str, int]
    """Max likes per platform per day. Keys: 'facebook', 'instagram'."""

    @classmethod
    def from_config(cls, config: dict[str, object]) -> EngagementPolicy:
        """Build from social-automation `config.json`.

        Reads `content_analysis.{relevance_threshold,approval_threshold}` for
        gates, and `rate_limits.{facebook,instagram}.{comments,likes}_per_day`
        for quotas. Anything missing falls back to the module-level defaults,
        which match current production behavior.

        Logs the resolved policy at INFO so production runs surface their
        active tuning in launchd output.
        """
        content_analysis = _as_mapping(config.get("content_analysis"))
        rate_limits = _as_mapping(config.get("rate_limits"))
        fb_limits = _as_mapping(rate_limits.get("facebook"))
        ig_limits = _as_mapping(rate_limits.get("instagram"))

        candidate_threshold = _as_float(content_analysis.get("relevance_threshold"), 0.70)
        approval_threshold = _as_float(content_analysis.get("approval_threshold"), 0.80)
        comment_threshold = _as_float(
            content_analysis.get("ig_comment_threshold"),
            _DEFAULT_IG_COMMENT_THRESHOLD,
        )

        daily_comment_quota: dict[str, int] = {
            "facebook": _as_int(fb_limits.get("comments_per_day"), _DEFAULT_FB_COMMENT_QUOTA),
            "instagram": _as_int(ig_limits.get("comments_per_day"), _DEFAULT_IG_COMMENT_QUOTA),
        }
        daily_like_quota: dict[str, int] = {
            "facebook": _as_int(fb_limits.get("likes_per_day"), _DEFAULT_FB_LIKE_QUOTA),
            "instagram": _as_int(ig_limits.get("likes_per_day"), _DEFAULT_IG_LIKE_QUOTA),
        }

        policy = cls(
            candidate_threshold=candidate_threshold,
            comment_threshold=comment_threshold,
            approval_threshold=approval_threshold,
            daily_comment_quota=daily_comment_quota,
            daily_like_quota=daily_like_quota,
        )

        _log.info(
            "engagement_policy_loaded",
            extra={
                "candidate_threshold": policy.candidate_threshold,
                "comment_threshold": policy.comment_threshold,
                "approval_threshold": policy.approval_threshold,
                "daily_comment_quota": policy.daily_comment_quota,
                "daily_like_quota": policy.daily_like_quota,
            },
        )

        return policy

    def is_candidate(self, score: float) -> bool:
        """True iff `score` qualifies a post for the candidate pool / like."""
        return score >= self.candidate_threshold

    def is_comment_candidate(self, score: float) -> bool:
        """True iff `score` qualifies a post for the comment queue."""
        return score >= self.comment_threshold

    def requires_approval(self, score: float) -> bool:
        """True iff a queued item must be flagged for manual approval."""
        return score < self.approval_threshold
