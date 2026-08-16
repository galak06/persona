"""Tests for lib.engagement.policy.

Covers:
    - `from_enforced_limits` derives quotas from the artifact `lib.rate_limiter`
      obeys, so the policy and the limiter cannot disagree
    - `thresholds_from_config` still reads brand-tunable scoring gates
    - boundary semantics for is_candidate / is_comment_candidate / requires_approval
    - frozen dataclass invariant (no runtime mutation)

Supersedes the `TestFromConfig` and `TestFromProfiles` suites. `from_config`
read a second copy of the quotas out of `config.json` and drifted from the
enforced values; `from_profiles` (ADR 0004) never acquired a production caller.
Both are gone — see ADR 0004's amendment.
"""

from __future__ import annotations

import dataclasses

import pytest

from lib.engagement.policy import EngagementPolicy, thresholds_from_config

# The artifact's own `<platform>:<action>` shape, matching data/rate_limits.json.
_LIMITS: dict[str, int] = {
    "facebook:comment": 5,
    "facebook:like": 5,
    "facebook:group_post": 10,
    "instagram:comment": 10,
    "instagram:like": 20,
    "wordpress:reply": 20,
}


def _policy(**kw: object) -> EngagementPolicy:
    return EngagementPolicy.from_enforced_limits(limits=dict(_LIMITS), **kw)  # type: ignore[arg-type]


class TestFromEnforcedLimits:
    def test_quotas_come_from_the_limits_map(self) -> None:
        policy = _policy()
        assert policy.daily_comment_quota == {"facebook": 5, "instagram": 10}
        assert policy.daily_like_quota == {"facebook": 5, "instagram": 20}

    def test_non_quota_actions_are_ignored(self) -> None:
        """`group_post` and `reply` are limiter concerns, not policy quotas."""
        policy = _policy()
        assert "wordpress" not in policy.daily_like_quota
        assert set(policy.daily_comment_quota) == {"facebook", "instagram"}
        assert policy.daily_comment_quota.get("wordpress") is None

    def test_a_platform_without_a_limit_is_absent_not_defaulted(self) -> None:
        """Absence must mean "off", never a hardcoded fallback.

        `like_step` gates on `daily_like_quota.get(platform, 0) <= 0`, so an
        unconfigured platform stays off instead of silently running on a
        number nobody set.
        """
        policy = EngagementPolicy.from_enforced_limits(limits={"facebook:comment": 5})
        assert policy.daily_like_quota == {}
        assert policy.daily_like_quota.get("facebook", 0) == 0

    def test_thresholds_default_when_not_supplied(self) -> None:
        policy = _policy()
        assert policy.candidate_threshold == 0.70
        assert policy.comment_threshold == 0.75
        assert policy.approval_threshold == 0.80

    def test_explicit_thresholds_win(self) -> None:
        policy = _policy(thresholds={"candidate_threshold": 0.55, "approval_threshold": 0.9})
        assert policy.candidate_threshold == 0.55
        assert policy.approval_threshold == 0.9
        assert policy.comment_threshold == 0.75  # untouched key keeps its default

    def test_the_policy_matches_the_real_artifact(self) -> None:
        """The invariant the whole change exists for.

        Built with no injected limits, the policy's quotas must equal what
        `can_act()` enforces. These were two independently-loaded copies that
        the code documented as drifting.
        """
        from lib.rate_limiter import DAILY_LIMITS, daily_limit

        policy = EngagementPolicy.from_enforced_limits()
        for platform, quota in policy.daily_comment_quota.items():
            assert quota == daily_limit(platform, "comment")
        for platform, quota in policy.daily_like_quota.items():
            assert quota == daily_limit(platform, "like")
        assert policy.daily_comment_quota, f"artifact had no comment limits: {DAILY_LIMITS}"


class TestThresholdsFromConfig:
    def test_reads_the_content_analysis_block(self) -> None:
        got = thresholds_from_config(
            {"content_analysis": {"relevance_threshold": 0.66, "approval_threshold": 0.88}}
        )
        assert got["candidate_threshold"] == 0.66
        assert got["approval_threshold"] == 0.88

    def test_ig_comment_threshold_defaults_to_0_75(self) -> None:
        assert thresholds_from_config({})["comment_threshold"] == 0.75

    def test_explicit_ig_comment_threshold_is_respected(self) -> None:
        got = thresholds_from_config({"content_analysis": {"ig_comment_threshold": 0.9}})
        assert got["comment_threshold"] == 0.9

    def test_a_config_without_content_analysis_yields_defaults(self) -> None:
        got = thresholds_from_config({})
        assert (got["candidate_threshold"], got["approval_threshold"]) == (0.70, 0.80)

    def test_a_rate_limits_block_is_ignored(self) -> None:
        """Quotas in config.json are inert now; only thresholds are read."""
        got = thresholds_from_config({"rate_limits": {"instagram": {"likes_per_day": 999}}})
        assert "daily_like_quota" not in got


class TestIsCandidate:
    def test_is_candidate_at_boundary(self) -> None:
        assert _policy().is_candidate(0.70) is True

    def test_is_candidate_below_boundary(self) -> None:
        assert _policy().is_candidate(0.6999) is False


class TestIsCommentCandidate:
    def test_is_comment_candidate_uses_comment_threshold(self) -> None:
        policy = _policy()
        assert policy.is_comment_candidate(0.75) is True
        assert policy.is_comment_candidate(0.74) is False


class TestRequiresApproval:
    def test_requires_approval_borderline(self) -> None:
        policy = _policy()
        assert policy.requires_approval(0.79) is True
        assert policy.requires_approval(0.80) is False


class TestFrozen:
    def test_policy_is_frozen(self) -> None:
        policy = _policy()
        with pytest.raises(dataclasses.FrozenInstanceError):
            policy.candidate_threshold = 0.1  # type: ignore[misc]
