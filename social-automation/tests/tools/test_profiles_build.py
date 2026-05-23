"""Tests for tools.profiles_build — profile loading + artifact composition."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.profiles_build import (
    _GENERATED_BY,
    _GENERATED_BY_SCHEDULE,
    _main,
    build_delay_ranges,
    build_flows,
    build_rate_limits,
    check_artifact,
    compose_artifact,
    compose_rate_limits_artifact,
    compose_schedule_artifact,
    load_profiles,
    validate_dag,
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

    def test_underscore_files_keyed_with_underscore_prefix(self, tmp_path: Path) -> None:
        # Slice B change: underscore profiles are now INCLUDED (for build_flows)
        # but keyed with a leading underscore so build_rate_limits can skip them.
        profiles_dir = tmp_path / "profiles"
        _write_profile(profiles_dir, "facebook.json", {"platform": "facebook", "rate_limits": {}})
        _write_profile(profiles_dir, "_engine.json", {"platform": "engine", "flows": []})

        loaded = load_profiles(profiles_dir)

        assert set(loaded.keys()) == {"facebook", "_engine"}

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

    def test_skips_underscore_profiles(self) -> None:
        # Asymmetry: build_rate_limits SKIPS _* profiles; build_flows INCLUDES them.
        profiles = {
            "facebook": {"platform": "facebook", "rate_limits": {"comments_per_day": 5}},
            "_engine": {"platform": "engine", "rate_limits": {"comments_per_day": 99}},
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

    def test_back_compat_alias_matches_rate_limits_composer(self) -> None:
        # compose_artifact is a back-compat alias for compose_rate_limits_artifact.
        # Both must produce identical output to keep slice A callers working.
        profiles = {
            "facebook": {"platform": "facebook", "rate_limits": {"comments_per_day": 5}},
        }
        assert compose_artifact(profiles) == compose_rate_limits_artifact(profiles)


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


class TestBuildFlows:
    def test_aggregates_across_profiles(self) -> None:
        profiles = {
            "facebook": {
                "platform": "facebook",
                "flows": [{"id": "fb_scan", "order": 10}],
            },
            "instagram": {
                "platform": "instagram",
                "flows": [{"id": "ig_scan", "order": 20}],
            },
        }

        flows = build_flows(profiles)

        ids = [f["id"] for f in flows]
        assert ids == ["fb_scan", "ig_scan"]

    def test_includes_engine_profile(self) -> None:
        # Asymmetry vs. build_rate_limits: build_flows INCLUDES _engine.
        profiles = {
            "facebook": {"platform": "facebook", "flows": [{"id": "fb_a", "order": 50}]},
            "_engine": {
                "platform": "engine",
                "flows": [
                    {"id": "site_analyze", "order": 5},
                    {"id": "wp_publish", "order": 30},
                    {"id": "cleanup", "order": 90},
                ],
            },
        }

        flows = build_flows(profiles)
        ids = [f["id"] for f in flows]

        assert "site_analyze" in ids
        assert "wp_publish" in ids
        assert "cleanup" in ids
        assert len(flows) == 4

    def test_sorts_by_order_field(self) -> None:
        profiles = {
            "p": {
                "platform": "p",
                "flows": [
                    {"id": "c", "order": 30},
                    {"id": "a", "order": 10},
                    {"id": "b", "order": 20},
                ],
            },
        }

        flows = build_flows(profiles)

        assert [f["id"] for f in flows] == ["a", "b", "c"]

    def test_returns_shallow_copies(self) -> None:
        # Caller mutations must not bleed back into the source profiles.
        original = {"id": "x", "order": 1}
        profiles = {"p": {"platform": "p", "flows": [original]}}

        flows = build_flows(profiles)
        flows[0]["mutated"] = True

        assert "mutated" not in original


class TestValidateDag:
    def test_returns_true_on_valid_chain(self) -> None:
        flows = [
            {"id": "a"},
            {"id": "b", "depends_on": ["a"]},
            {"id": "c", "depends_on": ["b"]},
        ]

        ok, reason = validate_dag(flows)

        assert ok is True
        assert reason == ""

    def test_detects_missing_dep(self) -> None:
        flows = [{"id": "a", "depends_on": ["nonexistent"]}]

        ok, reason = validate_dag(flows)

        assert ok is False
        assert "missing" in reason
        assert "nonexistent" in reason

    def test_detects_cycle(self) -> None:
        flows = [
            {"id": "a", "depends_on": ["b"]},
            {"id": "b", "depends_on": ["a"]},
        ]

        ok, reason = validate_dag(flows)

        assert ok is False
        assert "Cycle" in reason

    def test_detects_duplicate_id(self) -> None:
        flows = [{"id": "x"}, {"id": "x"}]

        ok, reason = validate_dag(flows)

        assert ok is False
        assert "Duplicate" in reason
        assert "x" in reason

    def test_handles_empty_flows(self) -> None:
        ok, reason = validate_dag([])

        assert ok is True
        assert reason == ""


class TestComposeScheduleArtifact:
    def test_has_generated_header(self) -> None:
        artifact = compose_schedule_artifact({})

        assert artifact["_generated"] == _GENERATED_BY_SCHEDULE
        assert "tasks" in artifact
        assert artifact["tasks"] == []

    def test_tasks_sorted_by_order(self) -> None:
        profiles = {
            "p": {
                "platform": "p",
                "flows": [{"id": "z", "order": 99}, {"id": "a", "order": 1}],
            },
        }

        artifact = compose_schedule_artifact(profiles)

        assert [t["id"] for t in artifact["tasks"]] == ["a", "z"]


class TestMain:
    def test_writes_artifact_on_default_invocation(self, tmp_path: Path) -> None:
        profile_dir = tmp_path / "profiles"
        _write_profile(
            profile_dir,
            "facebook.json",
            {"platform": "facebook", "rate_limits": {"comments_per_day": 5}},
        )
        out = tmp_path / "rate_limits.json"
        sched_out = tmp_path / "schedule.json"

        rc = _main([
            "--profile-dir", str(profile_dir),
            "--rate-limits-out", str(out),
            "--schedule-out", str(sched_out),
        ])

        assert rc == 0
        assert out.exists()
        assert sched_out.exists()
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
        sched_out = tmp_path / "schedule.json"
        # Write a stale artifact that does NOT match what compose_artifact
        # would produce from the profile.
        out.write_text(json.dumps({"_generated": "stale", "limits": {}, "delays": {}}))
        sched_out.write_text(json.dumps({"_generated": "stale", "tasks": []}))

        rc = _main([
            "--check",
            "--profile-dir", str(profile_dir),
            "--rate-limits-out", str(out),
            "--schedule-out", str(sched_out),
        ])

        assert rc == 1

    def test_check_returns_zero_when_in_sync(self, tmp_path: Path) -> None:
        profile_dir = tmp_path / "profiles"
        _write_profile(
            profile_dir,
            "facebook.json",
            {"platform": "facebook", "rate_limits": {"comments_per_day": 5}},
        )
        out = tmp_path / "rate_limits.json"
        sched_out = tmp_path / "schedule.json"

        # Generate both artifacts first, then re-check.
        assert _main([
            "--profile-dir", str(profile_dir),
            "--rate-limits-out", str(out),
            "--schedule-out", str(sched_out),
        ]) == 0
        rc = _main([
            "--check",
            "--profile-dir", str(profile_dir),
            "--rate-limits-out", str(out),
            "--schedule-out", str(sched_out),
        ])

        assert rc == 0

    def test_check_detects_schedule_drift(self, tmp_path: Path) -> None:
        # Profile has one flow; on-disk schedule.json has a different one.
        profile_dir = tmp_path / "profiles"
        _write_profile(
            profile_dir,
            "facebook.json",
            {
                "platform": "facebook",
                "rate_limits": {},
                "flows": [{"id": "fb_scan", "order": 10}],
            },
        )
        out = tmp_path / "rate_limits.json"
        sched_out = tmp_path / "schedule.json"
        # Pre-populate rate_limits.json correctly so only the schedule drifts.
        _main([
            "--profile-dir", str(profile_dir),
            "--rate-limits-out", str(out),
            "--schedule-out", str(sched_out),
        ])
        # Now stomp the schedule with a stale version.
        sched_out.write_text(json.dumps({
            "_generated": _GENERATED_BY_SCHEDULE,
            "tasks": [{"id": "wrong_flow", "order": 99}],
        }))

        rc = _main([
            "--check",
            "--profile-dir", str(profile_dir),
            "--rate-limits-out", str(out),
            "--schedule-out", str(sched_out),
        ])

        assert rc == 1

    def test_validate_dag_flag_exits_zero_on_valid(self, tmp_path: Path) -> None:
        profile_dir = tmp_path / "profiles"
        _write_profile(
            profile_dir,
            "facebook.json",
            {
                "platform": "facebook",
                "flows": [
                    {"id": "a", "order": 1},
                    {"id": "b", "order": 2, "depends_on": ["a"]},
                ],
            },
        )

        rc = _main(["--validate-dag", "--profile-dir", str(profile_dir)])

        assert rc == 0

    def test_validate_dag_flag_exits_nonzero_on_cycle(self, tmp_path: Path) -> None:
        profile_dir = tmp_path / "profiles"
        _write_profile(
            profile_dir,
            "facebook.json",
            {
                "platform": "facebook",
                "flows": [
                    {"id": "a", "order": 1, "depends_on": ["b"]},
                    {"id": "b", "order": 2, "depends_on": ["a"]},
                ],
            },
        )

        rc = _main(["--validate-dag", "--profile-dir", str(profile_dir)])

        assert rc == 1

    def test_write_aborts_when_dag_invalid(self, tmp_path: Path) -> None:
        # If the DAG is invalid, schedule.json must NOT be written.
        profile_dir = tmp_path / "profiles"
        _write_profile(
            profile_dir,
            "facebook.json",
            {
                "platform": "facebook",
                "flows": [{"id": "a", "depends_on": ["missing"]}],
            },
        )
        out = tmp_path / "rate_limits.json"
        sched_out = tmp_path / "schedule.json"

        rc = _main([
            "--profile-dir", str(profile_dir),
            "--rate-limits-out", str(out),
            "--schedule-out", str(sched_out),
        ])

        assert rc == 1
        assert not sched_out.exists()
