"""EngagementPolicy — thresholds and quotas for OutboundEngagement, in one object.

Holds the scoring gates (candidate, comment, approval) and the daily action
quotas (comment, like) in one frozen dataclass, built once at scanner startup
and read-only thereafter.

The two halves come from different places, deliberately:

* **Quotas** come from `data/rate_limits.json` — the artifact
  `python -m tools.profiles_build` generates from `profiles/<platform>.json`
  and `lib.rate_limiter` enforces. Reading the enforced copy is the point: the
  policy used to load its own from `config.json`, and the two drifted badly
  enough that `scripts/fb_engager.py` and `lib/rate_limiter.py` each carried a
  comment telling callers which to believe.
* **Thresholds** stay brand-tunable in `config.json`'s `content_analysis`
  block, via `thresholds_from_config`. Profiles do not carry scoring gates yet
  (ADR 0004 slice C).

Replaces three patterns that drifted across the FB and IG scanners:
    - `config["content_analysis"]["relevance_threshold"]` inline reads
    - `config["content_analysis"]["approval_threshold"]` inline reads
    - `ig_comment_threshold = 0.75` hardcoded in ig_engager

Design notes:
    - Frozen dataclass → fail loud if anything tries to mutate at runtime.
    - `from_enforced_limits` is the one entry point — never construct directly
      in production code.
    - Logs construction at INFO so production runs surface the resolved policy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

_log = logging.getLogger(__name__)

# Baseline threshold defaults. Slice C will fold these into the profile
# JSONs; until then callers pass `thresholds` and omitted keys land here.
_DEFAULT_THRESHOLDS: dict[str, float] = {
    "candidate_threshold": 0.70,
    "comment_threshold": 0.75,
    "approval_threshold": 0.80,
    "ig_comment_threshold": 0.75,
}

# Defaults — match current production behavior (CLAUDE.md rate-limits + ig_engager
# hardcode). Anything missing from config.json falls back to these, so the
# policy is constructible from a minimal config.
_DEFAULT_IG_COMMENT_THRESHOLD: float = 0.75


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


def thresholds_from_config(config: dict[str, object]) -> dict[str, float]:
    """Scoring gates from a brand's `config.json` `content_analysis` block.

    Thresholds are still brand-tunable; only the QUOTAS moved to profiles.
    Kept here rather than in each engager so the two cannot disagree about
    which config keys mean which gate — they did before, with `ig_engager`
    hardcoding its comment threshold.
    """
    content_analysis = _as_mapping(config.get("content_analysis"))
    return {
        "candidate_threshold": _as_float(
            content_analysis.get("relevance_threshold"),
            _DEFAULT_THRESHOLDS["candidate_threshold"],
        ),
        "approval_threshold": _as_float(
            content_analysis.get("approval_threshold"),
            _DEFAULT_THRESHOLDS["approval_threshold"],
        ),
        "comment_threshold": _as_float(
            content_analysis.get("ig_comment_threshold"),
            _DEFAULT_THRESHOLDS["comment_threshold"],
        ),
    }


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
    def from_enforced_limits(
        cls,
        *,
        thresholds: dict[str, float] | None = None,
        limits: dict[str, int] | None = None,
    ) -> EngagementPolicy:
        """Quotas from the SAME artifact `lib.rate_limiter` enforces.

        This is the one constructor. It replaces two:

        * ``from_config(config.json)``, which read a second copy of the quotas
          out of the brand's config. That copy drifted from the enforced one —
          both `scripts/fb_engager.py` and `lib/rate_limiter.py` carried
          comments telling callers which of the two to believe, and
          `brands/_shared/config.json` still shipped Instagram 8/2 against an
          enforced 20/10, so every newly provisioned brand was born forked.
        * ``from_profiles(profile_dir)``, added by ADR 0004 for an
          opportunistic migration that never happened — it had zero production
          callers. Reading `profiles/` directly would have been a *third*
          reader of the same numbers; reading the generated artifact is the
          same data with the guarantee that it is the copy `can_act()` obeys.

        ADR 0004 is honoured, not bypassed: `profiles/<platform>.json` remains
        canonical, `data/rate_limits.json` remains the derived lockfile that
        `python -m tools.profiles_build` regenerates and CI `--check`s. This
        just stops the policy maintaining a private second opinion.

        A platform with no configured limit is **absent** from the quota dicts
        rather than defaulted. Callers use `.get(platform, 0)` and treat 0 as
        "not enabled", so an unconfigured platform is off rather than silently
        running on a hardcoded number.

        `thresholds` stays an argument: profiles do not carry scoring gates
        yet (ADR 0004 slice C). Callers pass the brand's `content_analysis`
        values; omitted keys fall back to `_DEFAULT_THRESHOLDS`.
        """
        if limits is None:
            from lib.rate_limiter import DAILY_LIMITS

            limits = DAILY_LIMITS

        resolved = dict(_DEFAULT_THRESHOLDS)
        if thresholds is not None:
            resolved.update({k: v for k, v in thresholds.items() if v is not None})

        daily_comment_quota: dict[str, int] = {}
        daily_like_quota: dict[str, int] = {}
        for key, value in limits.items():
            platform, _, action = key.partition(":")
            if action == "comment":
                daily_comment_quota[platform] = value
            elif action == "like":
                daily_like_quota[platform] = value

        policy = cls(
            candidate_threshold=resolved["candidate_threshold"],
            comment_threshold=resolved["comment_threshold"],
            approval_threshold=resolved["approval_threshold"],
            daily_comment_quota=daily_comment_quota,
            daily_like_quota=daily_like_quota,
        )

        _log.info(
            "engagement_policy_loaded",
            extra={
                "source": "data/rate_limits.json (derived from profiles/)",
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
