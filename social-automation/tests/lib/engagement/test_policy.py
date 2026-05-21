"""Tests for lib.engagement.policy.

Covers:
    - from_config reads thresholds and quotas from the production schema
    - sensible defaults when optional keys are missing
    - boundary semantics for is_candidate / is_comment_candidate / requires_approval
    - frozen dataclass invariant (no runtime mutation)
    - slice 3 invariants: FB like quota = 0, IG comment quota = 10
"""

from __future__ import annotations

import dataclasses

import pytest

from lib.engagement.policy import EngagementPolicy


def _production_like_config() -> dict[str, object]:
    """A dict matching social-automation/config.json shape (subset used by policy)."""
    return {
        "content_analysis": {
            "relevance_threshold": 0.70,
            "approval_threshold": 0.80,
        },
        "rate_limits": {
            "facebook": {
                "comments_per_day": 5,
            },
            "instagram": {
                "comments_per_day": 10,
                "likes_per_day": 8,
            },
        },
    }


class TestFromConfig:
    def test_from_config_reads_thresholds(self) -> None:
        policy = EngagementPolicy.from_config(_production_like_config())

        assert policy.candidate_threshold == 0.70
        assert policy.approval_threshold == 0.80
        assert policy.comment_threshold == 0.75  # default
        assert policy.daily_comment_quota == {"facebook": 5, "instagram": 10}
        assert policy.daily_like_quota == {"facebook": 0, "instagram": 8}

    def test_from_config_defaults_ig_comment_threshold_to_0_75(self) -> None:
        config = _production_like_config()
        # explicitly omit ig_comment_threshold from content_analysis
        content_analysis = config["content_analysis"]
        assert isinstance(content_analysis, dict)
        assert "ig_comment_threshold" not in content_analysis

        policy = EngagementPolicy.from_config(config)

        assert policy.comment_threshold == 0.75

    def test_from_config_respects_explicit_ig_comment_threshold(self) -> None:
        config = _production_like_config()
        config["content_analysis"] = {
            "relevance_threshold": 0.70,
            "approval_threshold": 0.80,
            "ig_comment_threshold": 0.82,
        }

        policy = EngagementPolicy.from_config(config)

        assert policy.comment_threshold == 0.82

    def test_from_config_uses_defaults_when_rate_limits_missing(self) -> None:
        # No rate_limits key at all -> falls back to per-platform defaults
        config: dict[str, object] = {
            "content_analysis": {
                "relevance_threshold": 0.70,
                "approval_threshold": 0.80,
            }
        }

        policy = EngagementPolicy.from_config(config)

        assert policy.daily_comment_quota == {"facebook": 5, "instagram": 10}
        assert policy.daily_like_quota == {"facebook": 0, "instagram": 8}


class TestIsCandidate:
    def test_is_candidate_at_boundary(self) -> None:
        policy = EngagementPolicy.from_config(_production_like_config())
        # candidate_threshold == 0.70 -> exactly 0.70 is included (>= semantics)
        assert policy.is_candidate(0.70) is True

    def test_is_candidate_below_boundary(self) -> None:
        policy = EngagementPolicy.from_config(_production_like_config())
        assert policy.is_candidate(0.6999) is False


class TestIsCommentCandidate:
    def test_is_comment_candidate_uses_comment_threshold(self) -> None:
        # Comment threshold (0.75) is stricter than candidate threshold (0.70).
        # A score that's a candidate need not be a comment candidate.
        policy = EngagementPolicy.from_config(_production_like_config())

        assert policy.is_candidate(0.72) is True
        assert policy.is_comment_candidate(0.72) is False

        assert policy.is_comment_candidate(0.75) is True
        assert policy.is_comment_candidate(0.74999) is False


class TestRequiresApproval:
    def test_requires_approval_borderline(self) -> None:
        policy = EngagementPolicy.from_config(_production_like_config())
        # approval_threshold == 0.80 -> below requires approval, at-or-above auto.
        assert policy.requires_approval(0.79999) is True
        assert policy.requires_approval(0.80) is False
        assert policy.requires_approval(0.95) is False


class TestFrozen:
    def test_policy_is_frozen(self) -> None:
        policy = EngagementPolicy.from_config(_production_like_config())
        with pytest.raises(dataclasses.FrozenInstanceError):
            policy.candidate_threshold = 0.99  # type: ignore[misc]


class TestSlice1Invariants:
    """Behavior gates locking in current production defaults.

    Future slices will bump these (slice 4 adds FB inline liking, possibly
    bumps IG to 10 comments/day). When that happens, these tests are the
    visible spot to update.
    """

    def test_default_daily_like_quota_facebook_is_zero(self) -> None:
        policy = EngagementPolicy.from_config(_production_like_config())
        assert policy.daily_like_quota["facebook"] == 0

    def test_default_daily_comment_quota_instagram_is_ten(self) -> None:
        policy = EngagementPolicy.from_config(_production_like_config())
        assert policy.daily_comment_quota["instagram"] == 10
