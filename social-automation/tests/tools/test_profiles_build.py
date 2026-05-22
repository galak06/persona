"""Tests for tools.profiles_build — profile loading + artifact composition."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.profiles_build import (
    _GENERATED_BY,
    _main,
    build_delay_ranges,
    build_rate_limits,
    check_artifact,
    compose_artifact,
    load_profiles,
    write_artifact,
)


def _write_profile(profile_dir: Path, name: str, payload: dict) -> Path:
    """Helper: write a profile JSON to a fixture dir."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    path = profile_dir / name
    path.write_text(json.dumps(payload))
    return path


class TestLoadProfiles:
    def test_reads_all_json_files(self, tmp_path: Path) -> None:
        profiles_dir = tmp_path / "profiles"
        _write_profile(profiles_dir, "facebook.json", {"platform": "facebook", "rate_limits": {}})
        _write_profile(profiles_dir, "instagram.json", {"platform": "instagram", "rate_limits": {}})

        loaded = load_profiles(profiles_dir)

        assert set(loaded.keys()) == {"facebook", "instagram"}

    def test_skips_underscore_files(self, tmp_path: Path) -> None:
        profiles_dir = tmp_path / "profiles"
        _write_profile(profiles_dir, "facebook.json", {"platform": "facebook", "rate_limits": {}})
        _write_profile(profiles_dir, "_draft.json", {"platform": "draft", "rate_limits": {}})

        loaded = load_profiles(profiles_dir)

        assert set(loaded.keys()) == {"facebook"}

    def test_raises_when_platform_field_missing(self, tmp_path: Path) -> None:
        profiles_dir = tmp_path / "profiles"
        _write_profile(profiles_dir, "broken.json", {"rate_limits": {}})

        with pytest.raises(ValueError, match="missing string 'platform'"):
            load_profiles(profiles_dir)


class TestBuildRateLimits:
    def test_flattens_to_platform_action_keys(self, tmp_path: Path) -> None:
        profiles = {
            "facebook": {"platform": "facebook", "rate_limits": {"comments_per_day": 5}},
        }

        limits = build_rate_limits(profiles)

        assert limits == {"facebook:comment": 5}

    def test_skips_weekly_limits(self, tmp_path: Path) -> None:
        profiles = {
            "facebook": {
                "platform": "facebook",
                "rate_limits": {
                    "group_join_requests_per_week": 15,
                    "comments_per_day": 5,
                },
            },
        }

        limits = build_rate_limits(profiles)

        assert "facebook:group_join_requests_per_week" not in limits
        assert "facebook:group_join_request" not in limits
        # Weekly cadence preserved in profile JSON only; not in the flat dict.
        assert limits == {"facebook:comment": 5}

    def test_drops_legacy_ig_prefix(self) -> None:
        profiles = {
            "instagram": {"platform": "instagram", "rate_limits": {"comments_per_day": 10}},
        }

        limits = build_rate_limits(profiles)

        assert "instagram:comment" in limits
        assert "instagram:ig_comment" not in limits
        assert limits["instagram:comment"] == 10

    def test_ignores_delay_fields(self) -> None:
        profiles = {
            "facebook": {
                "platform": "facebook",
                "rate_limits": {
                    "comments_per_day": 5,
                    "delay_between_comments": "30-120s random",
                },
            },
        }

        limits = build_rate_limits(profiles)

        assert limits == {"facebook:comment": 5}


class TestBuildDelayRanges:
    def test_collects_delay_fields(self) -> None:
        profiles = {
            "instagram": {
                "platform": "instagram",
                "rate_limits": {"delay_between_likes": "10-45s random"},
            },
        }

        delays = build_delay_ranges(profiles)

        assert delays == {"instagram:like": "10-45s random"}

    def test_skips_non_delay_fields(self) -> None:
        profiles = {
            "facebook": {
                "platform": "facebook",
                "rate_limits": {
                    "comments_per_day": 5,
                    "delay_between_comments": "30-120s random",
                },
            },
        }

        delays = build_delay_ranges(profiles)

        assert delays == {"facebook:comment": "30-120s random"}


class TestComposeArtifact:
    def test_has_generated_header(self) -> None:
        # _generated MUST be a fixed string (not a timestamp) so the
        # lockfile is deterministic and --check is idempotent.
        artifact = compose_artifact({})

        assert "_generated" in artifact
        assert artifact["_generated"] == _GENERATED_BY
        assert isinstance(artifact["_generated"], str)

    def test_includes_limits_and_delays(self) -> None:
        profiles = {
            "instagram": {
                "platform": "instagram",
                "rate_limits": {
                    "likes_per_day": 8,
                    "delay_between_likes": "10-45s random",
                },
            },
        }

        artifact = compose_artifact(profiles)

        assert artifact["limits"] == {"instagram:like": 8}
        assert artifact["delays"] == {"instagram:like": "10-45s random"}


class TestCheckArtifact:
    def test_matches(self, tmp_path: Path) -> None:
        path = tmp_path / "rate_limits.json"
        expected = {"_generated": "x", "limits": {"a:b": 1}, "delays": {}}
        write_artifact(path, expected)

        assert check_artifact(path, expected) is True

    def test_detects_drift(self, tmp_path: Path) -> None:
        path = tmp_path / "rate_limits.json"
        expected = {"_generated": "x", "limits": {"a:b": 1}, "delays": {}}
        write_artifact(path, expected)

        # Mutate on-disk content so it no longer matches `expected`.
        path.write_text(json.dumps({"_generated": "x", "limits": {"a:b": 999}, "delays": {}}))

        assert check_artifact(path, expected) is False

    def test_returns_false_when_file_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "nope.json"

        assert check_artifact(path, {"_generated": "x"}) is False


class TestMain:
    def test_writes_artifact_on_default_invocation(self, tmp_path: Path) -> None:
        profile_dir = tmp_path / "profiles"
        _write_profile(
            profile_dir,
            "facebook.json",
            {"platform": "facebook", "rate_limits": {"comments_per_day": 5}},
        )
        out = tmp_path / "rate_limits.json"

        rc = _main(["--profile-dir", str(profile_dir), "--rate-limits-out", str(out)])

        assert rc == 0
        assert out.exists()
        artifact = json.loads(out.read_text())
        assert artifact["limits"] == {"facebook:comment": 5}
        assert artifact["_generated"] == _GENERATED_BY

    def test_check_exits_nonzero_on_drift(self, tmp_path: Path) -> None:
        profile_dir = tmp_path / "profiles"
        _write_profile(
            profile_dir,
            "facebook.json",
            {"platform": "facebook", "rate_limits": {"comments_per_day": 5}},
        )
        out = tmp_path / "rate_limits.json"
        # Write a stale artifact that does NOT match what compose_artifact
        # would produce from the profile.
        out.write_text(json.dumps({"_generated": "stale", "limits": {}, "delays": {}}))

        rc = _main(["--check", "--profile-dir", str(profile_dir), "--rate-limits-out", str(out)])

        assert rc == 1

    def test_check_returns_zero_when_in_sync(self, tmp_path: Path) -> None:
        profile_dir = tmp_path / "profiles"
        _write_profile(
            profile_dir,
            "facebook.json",
            {"platform": "facebook", "rate_limits": {"comments_per_day": 5}},
        )
        out = tmp_path / "rate_limits.json"
        # Generate the artifact first, then immediately re-check.
        assert _main(["--profile-dir", str(profile_dir), "--rate-limits-out", str(out)]) == 0
        rc = _main(["--check", "--profile-dir", str(profile_dir), "--rate-limits-out", str(out)])

        assert rc == 0
