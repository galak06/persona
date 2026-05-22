"""Tests for lib.engagement.policy.

Covers:
    - from_config reads thresholds and quotas from the production schema
    - sensible defaults when optional keys are missing
    - boundary semantics for is_candidate / is_comment_candidate / requires_approval
    - frozen dataclass invariant (no runtime mutation)
    - slice 4 invariants: FB like quota = 5, IG comment quota = 10
    - slice A: from_profiles reads quotas from profiles/<platform>.json
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

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
        assert policy.daily_like_quota == {"facebook": 5, "instagram": 8}

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
        assert policy.daily_like_quota == {"facebook": 5, "instagram": 8}


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

    Slice 4 activated FB inline liking (0 → 5/day). When future slices bump
    these further, these tests are the visible spot to update.
    """

    def test_default_daily_like_quota_facebook_is_five(self) -> None:
        policy = EngagementPolicy.from_config(_production_like_config())
        assert policy.daily_like_quota["facebook"] == 5

    def test_default_daily_comment_quota_instagram_is_ten(self) -> None:
        policy = EngagementPolicy.from_config(_production_like_config())
        assert policy.daily_comment_quota["instagram"] == 10


# Path to the real production profiles dir. tests/lib/engagement/test_policy.py
# is 3 parents deep relative to social-automation/, so .parents[3] / "profiles"
# lands on the canonical profile JSONs.
_PROFILES_DIR = Path(__file__).resolve().parents[3] / "profiles"


def _write_profile(dir_path: Path, name: str, payload: dict[str, object]) -> Path:
    """Write a profile JSON fixture; returns the path."""
    target = dir_path / name
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


class TestFromProfiles:
    def test_from_profiles_reads_quotas(self) -> None:
        policy = EngagementPolicy.from_profiles(_PROFILES_DIR)

        assert policy.daily_comment_quota == {"facebook": 5, "instagram": 10}
        assert policy.daily_like_quota == {"facebook": 5, "instagram": 8}

    def test_from_profiles_omits_wp_from_quota_dicts(self) -> None:
        policy = EngagementPolicy.from_profiles(_PROFILES_DIR)

        assert "wordpress" not in policy.daily_comment_quota
        assert "wordpress" not in policy.daily_like_quota

    def test_from_profiles_uses_default_thresholds_when_none(self) -> None:
        policy = EngagementPolicy.from_profiles(_PROFILES_DIR)

        # Matches the slice-1 baseline that from_config({}) produces today.
        assert policy.candidate_threshold == 0.70
        assert policy.comment_threshold == 0.75
        assert policy.approval_threshold == 0.80

    def test_from_profiles_accepts_explicit_thresholds(self) -> None:
        policy = EngagementPolicy.from_profiles(
            _PROFILES_DIR,
            thresholds={
                "candidate_threshold": 0.50,
                "comment_threshold": 0.60,
                "approval_threshold": 0.65,
            },
        )

        assert policy.candidate_threshold == 0.50
        assert policy.comment_threshold == 0.60
        assert policy.approval_threshold == 0.65

    def test_from_profiles_skips_underscore_files(self, tmp_path: Path) -> None:
        _write_profile(
            tmp_path,
            "_draft.json",
            {"platform": "draftplatform", "rate_limits": {"comments_per_day": 99}},
        )
        _write_profile(
            tmp_path,
            "facebook.json",
            {"platform": "facebook", "rate_limits": {"comments_per_day": 5, "likes_per_day": 5}},
        )

        policy = EngagementPolicy.from_profiles(tmp_path)

        assert policy.daily_comment_quota == {"facebook": 5}
        assert "draftplatform" not in policy.daily_comment_quota
        assert policy.daily_like_quota == {"facebook": 5}
