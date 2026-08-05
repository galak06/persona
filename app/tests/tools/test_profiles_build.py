"""Tests for tools.profiles_build — profile loading + artifact composition."""
# ruff: noqa: S101, RUF100  (pytest tests use `assert` by design; local ruff disables S)

from __future__ import annotations

import json
import plistlib
from pathlib import Path
from typing import Any

import pytest
from tools.launchd_plists import (
    compose_brand_plists as launchd_compose_brand_plists,
)
from tools.launchd_plists import (
    compose_plist_xml,
    cron_to_launchd,
    default_plist_paths,
    install_plists,
    resolve_plist_paths,
)
from tools.profiles_build import (
    _GENERATED_BY,
    _GENERATED_BY_BRAND_SCHEDULE,
    _GENERATED_BY_SCHEDULE,
    _main,
    build_delay_ranges,
    build_flows,
    build_rate_limits,
    check_artifact,
    compose_artifact,
    compose_brand_plists,
    compose_brand_schedule_artifact,
    compose_rate_limits_artifact,
    compose_schedule_artifact,
    load_brand_overlay,
    load_profiles,
    merge_brand_into_profiles,
    validate_dag,
    write_artifact,
)

# ---------------------------------------------------------------------------
# Slice D: launchd plist generation
# ---------------------------------------------------------------------------

_PLIST_BRAND_FIXTURE: dict[str, Any] = {
    "brand": {
        "name": "TestBrand",
        "key": "tbrand",
        "site": "https://example.test",
    },
}


def _make_task(
    task_id: str = "tbrand-fb-scanner",
    cron: str = "33 15 * * *",
    script: str | None = "scripts/fb_scan.py",
    skill: str = "fb-scanner",
) -> dict[str, Any]:
    """Helper: build a minimal task dict like the brand schedule emits."""
    task: dict[str, Any] = {
        "id": task_id,
        "skill": skill,
        "schedule": {"cron": cron, "cadence": "daily"},
    }
    if script is not None:
        task["script"] = script
    return task


class TestCronToLaunchd:
    def test_daily_at_time(self) -> None:
        assert cron_to_launchd("33 15 * * *") == {"Hour": 15, "Minute": 33}

    def test_weekly_sunday(self) -> None:
        assert cron_to_launchd("0 9 * * 0") == {"Hour": 9, "Minute": 0, "Weekday": 0}

    def test_multiple_hours(self) -> None:
        # Multiple comma-separated hours expand to a list of dicts so launchd
        # fires the agent at each hour with the same minute.
        result = cron_to_launchd("0 9,14,20 * * *")
        assert result == [
            {"Hour": 9, "Minute": 0},
            {"Hour": 14, "Minute": 0},
            {"Hour": 20, "Minute": 0},
        ]

    def test_hourly_range(self) -> None:
        # Hour range "8-22" expands to 15 entries (8..22 inclusive).
        result = cron_to_launchd("0 8-22 * * *")
        assert isinstance(result, list)
        assert len(result) == 15
        assert result[0] == {"Hour": 8, "Minute": 0}
        assert result[-1] == {"Hour": 22, "Minute": 0}

    def test_step_values_raise(self) -> None:
        with pytest.raises(NotImplementedError, match="step values"):
            cron_to_launchd("0 */2 * * *")

    def test_invalid_field_count_raises(self) -> None:
        # 6-field "seconds + cron" form is not supported.
        with pytest.raises(ValueError, match="5-field cron"):
            cron_to_launchd("0 0 15 * * *")


class TestComposePlistXml:
    def test_has_label(self) -> None:
        task = _make_task()
        payload = compose_plist_xml(task, _PLIST_BRAND_FIXTURE, default_plist_paths())
        plist = plistlib.loads(payload)
        assert plist["Label"] == "com.testbrand.fb-scanner"
        assert "ProgramArguments" in plist
        assert "StartCalendarInterval" in plist

    def test_environment_includes_brand_dir(self, tmp_path: Path) -> None:
        task = _make_task()
        paths = resolve_plist_paths(tmp_path)
        plist = plistlib.loads(compose_plist_xml(task, _PLIST_BRAND_FIXTURE, paths))
        env = plist["EnvironmentVariables"]
        assert env["BRAND_DIR"] == str(tmp_path)
        assert "PATH" in env
        assert env["PYTHONUNBUFFERED"] == "1"

    def test_runatload_false(self) -> None:
        plist = plistlib.loads(
            compose_plist_xml(_make_task(), _PLIST_BRAND_FIXTURE, default_plist_paths())
        )
        assert plist["RunAtLoad"] is False

    def test_claude_cli_fallback_for_scriptless_task(self) -> None:
        # Tasks with no `script` field are run via Claude CLI as a slash command.
        task = _make_task(
            task_id="tbrand-wp-comment-handler",
            script=None,
            skill="wp-comment-handler",
        )
        plist = plistlib.loads(
            compose_plist_xml(task, _PLIST_BRAND_FIXTURE, default_plist_paths())
        )
        prog_args = plist["ProgramArguments"]
        assert prog_args[1] == "--dangerously-skip-permissions"
        assert prog_args[2] == "/wp-comment-handler"


class TestComposeBrandPlists:
    def test_produces_one_per_task(self) -> None:
        # 3 distinct task ids -> 3 plist files. Re-use the launchd helper that
        # takes brand-schedule tasks directly so we don't depend on profiles.
        tasks = [
            _make_task("tbrand-a", "0 9 * * *", "scripts/a.py", "skill-a"),
            _make_task("tbrand-b", "0 10 * * *", "scripts/b.py", "skill-b"),
            _make_task("tbrand-c", "0 11 * * *", "scripts/c.py", "skill-c"),
        ]
        plists = launchd_compose_brand_plists(tasks, _PLIST_BRAND_FIXTURE)
        assert len(plists) == 3

    def test_filename_convention(self) -> None:
        tasks = [_make_task("tbrand-fb-scanner")]
        plists = launchd_compose_brand_plists(tasks, _PLIST_BRAND_FIXTURE)
        assert "com.testbrand.fb-scanner.plist" in plists

    def test_skips_tasks_with_no_cron(self) -> None:
        # On-demand tasks (no schedule.cron) should not produce a plist —
        # launchd only handles scheduled jobs.
        tasks = [_make_task("tbrand-a", "0 9 * * *", "scripts/a.py", "skill-a")]
        tasks.append({"id": "tbrand-on-demand", "skill": "x", "schedule": {}})
        plists = launchd_compose_brand_plists(tasks, _PLIST_BRAND_FIXTURE)
        assert len(plists) == 1

    def test_high_level_compose_brand_plists_wrapper(self) -> None:
        # The wrapper in profiles_build resolves brand-prefix tasks from
        # merged_profiles. Smoke-test that the wrapper returns a non-empty dict
        # when given a profile with one flow.
        profiles = {
            "facebook": {
                "platform": "facebook",
                "flows": [
                    {
                        "id": "fb-scanner",
                        "order": 10,
                        "skill": "fb-scanner",
                        "script": "scripts/fb_scan.py",
                        "schedule": {"cron": "33 15 * * *", "cadence": "daily"},
                    },
                ],
            },
        }
        plists = compose_brand_plists(profiles, _PLIST_BRAND_FIXTURE)
        assert "com.testbrand.fb-scanner.plist" in plists


class TestInstallSubcommand:
    def test_dry_run_no_filesystem_changes(self, tmp_path: Path) -> None:
        # Dry-run install must NEVER spawn launchctl or touch the LaunchAgents
        # directory. Capture every subprocess invocation through a stub runner.
        plist_dir = tmp_path / "launchd"
        plist_dir.mkdir()
        (plist_dir / "com.testbrand.foo.plist").write_bytes(b"<plist/>")
        launch_agents_dir = tmp_path / "LaunchAgents"  # intentionally missing

        captured: list[list[str]] = []

        def stub_runner(cmd: list[str], **_: object) -> object:
            captured.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        rc = install_plists(
            plist_dir,
            apply=False,
            launch_agents_dir=launch_agents_dir,
            runner=stub_runner,
        )

        assert rc == 0
        assert captured == []  # zero subprocess calls under dry-run
        assert not launch_agents_dir.exists()

    def test_apply_required_to_change_filesystem(self, tmp_path: Path) -> None:
        # Without --apply the plist file MUST NOT be copied into LaunchAgents.
        plist_dir = tmp_path / "launchd"
        plist_dir.mkdir()
        (plist_dir / "com.testbrand.foo.plist").write_bytes(b"<plist/>")
        launch_agents_dir = tmp_path / "LaunchAgents"
        launch_agents_dir.mkdir()

        def stub_runner(*_a: object, **_kw: object) -> object:
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        before = sorted(p.name for p in launch_agents_dir.iterdir())
        install_plists(
            plist_dir,
            apply=False,
            launch_agents_dir=launch_agents_dir,
            runner=stub_runner,
        )
        after = sorted(p.name for p in launch_agents_dir.iterdir())
        assert before == after == []

    def test_apply_copies_files_and_calls_launchctl(self, tmp_path: Path) -> None:
        # With --apply, both `launchctl` bootout + bootstrap fire AND the file
        # lands in the target dir.
        plist_dir = tmp_path / "launchd"
        plist_dir.mkdir()
        (plist_dir / "com.testbrand.foo.plist").write_bytes(b"<plist/>")
        launch_agents_dir = tmp_path / "LaunchAgents"

        captured: list[list[str]] = []

        def stub_runner(cmd: list[str], **_: object) -> object:
            captured.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        install_plists(
            plist_dir,
            apply=True,
            launch_agents_dir=launch_agents_dir,
            runner=stub_runner,
        )

        assert (launch_agents_dir / "com.testbrand.foo.plist").exists()
        assert any("bootstrap" in c for c in captured)
        assert any("bootout" in c for c in captured)


@pytest.fixture(autouse=True)
def _clear_brand_dir_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the BRAND_DIR env var (default for --brand-dir).

    Without this, tests inherit a host BRAND_DIR pointing at a real brand
    overlay and the engine artifacts unexpectedly include brand-overridden
    fields. Tests that exercise the brand path opt back in by passing
    `--brand-dir` explicitly.
    """
    monkeypatch.delenv("BRAND_DIR", raising=False)


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


# --- Slice C: brand overlay --------------------------------------------------

_BRAND_FIXTURE: dict = {
    "brand": {
        "name": "TestBrand",
        "key": "tbrand",
        "site": "https://example.test",
        "voice": {"persona": "Test Persona"},
    },
    "profiles": {
        "facebook": {"auth": {"page_id_env": "FB_PAGE_ID"}},
        "instagram": {
            "auth": {"account_id_env": "IG_ACCOUNT_ID"},
            "rate_limits": {
                "own_replies_per_day": 15,
                "delay_between_own_replies": "5-10s random",
            },
        },
    },
}


class TestLoadBrandOverlay:
    def test_missing_dir_returns_none(self) -> None:
        assert load_brand_overlay(None) is None

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        # brand_dir exists, but brand.json doesn't.
        assert load_brand_overlay(tmp_path) is None

    def test_reads_brand_json(self, tmp_path: Path) -> None:
        (tmp_path / "brand.json").write_text(json.dumps(_BRAND_FIXTURE))

        loaded = load_brand_overlay(tmp_path)

        assert loaded is not None
        assert loaded["brand"]["key"] == "tbrand"
        assert loaded["profiles"]["instagram"]["rate_limits"]["own_replies_per_day"] == 15


class TestMergeBrandIntoProfiles:
    def test_no_brand_returns_input(self) -> None:
        profiles = {"facebook": {"platform": "facebook", "rate_limits": {"comments_per_day": 5}}}

        merged = merge_brand_into_profiles(profiles, None)

        assert merged == profiles
        # Returned dict must not be the same object (caller-mutation safety).
        merged["facebook"]["rate_limits"]["comments_per_day"] = 99
        assert profiles["facebook"]["rate_limits"]["comments_per_day"] == 5

    def test_overrides_rate_limit(self) -> None:
        profiles = {
            "facebook": {"platform": "facebook", "rate_limits": {"comments_per_day": 5}},
        }
        brand = {
            "brand": {"key": "x"},
            "profiles": {"facebook": {"rate_limits": {"comments_per_day": 3}}},
        }

        merged = merge_brand_into_profiles(profiles, brand)

        assert merged["facebook"]["rate_limits"]["comments_per_day"] == 3
        # Input unchanged.
        assert profiles["facebook"]["rate_limits"]["comments_per_day"] == 5

    def test_adds_auth_block(self) -> None:
        profiles = {
            "facebook": {"platform": "facebook", "rate_limits": {}},
        }
        brand = {
            "brand": {"key": "x"},
            "profiles": {"facebook": {"auth": {"page_id_env": "FB_PAGE_ID"}}},
        }

        merged = merge_brand_into_profiles(profiles, brand)

        assert merged["facebook"]["auth"] == {"page_id_env": "FB_PAGE_ID"}

    def test_preserves_other_keys(self) -> None:
        # Brand only touches rate_limits; flows + other keys survive untouched.
        profiles = {
            "facebook": {
                "platform": "facebook",
                "rate_limits": {"comments_per_day": 5},
                "flows": [{"id": "fb_scan", "order": 10}],
            },
        }
        brand = {
            "brand": {"key": "x"},
            "profiles": {"facebook": {"rate_limits": {"comments_per_day": 2}}},
        }

        merged = merge_brand_into_profiles(profiles, brand)

        assert merged["facebook"]["flows"] == [{"id": "fb_scan", "order": 10}]
        assert merged["facebook"]["platform"] == "facebook"
        assert merged["facebook"]["rate_limits"]["comments_per_day"] == 2

    def test_stashes_brand_metadata_at__brand(self) -> None:
        profiles: dict = {}
        brand = {
            "brand": {"name": "TestBrand", "key": "tbrand", "site": "https://x.test"},
            "profiles": {},
        }

        merged = merge_brand_into_profiles(profiles, brand)

        assert merged["_brand"]["name"] == "TestBrand"
        assert merged["_brand"]["key"] == "tbrand"
        assert merged["_brand"]["site"] == "https://x.test"

    def test_unknown_platform_in_brand_is_added(self) -> None:
        # New platform from brand overlay (unusual but supported).
        profiles: dict = {
            "facebook": {"platform": "facebook", "rate_limits": {}},
        }
        brand = {
            "brand": {"key": "x"},
            "profiles": {"newplatform": {"rate_limits": {"comments_per_day": 1}}},
        }

        merged = merge_brand_into_profiles(profiles, brand)

        assert "newplatform" in merged
        assert merged["newplatform"]["rate_limits"]["comments_per_day"] == 1


class TestComposeBrandScheduleArtifact:
    def test_applies_prefix(self) -> None:
        merged = {
            "facebook": {"platform": "facebook", "flows": [{"id": "fb-scanner", "order": 10}]},
            "_brand": {"key": "dogfood"},
        }
        brand = {"brand": {"key": "dogfood"}}

        artifact = compose_brand_schedule_artifact(merged, brand)

        ids = [t["id"] for t in artifact["tasks"]]
        assert ids == ["dogfood-fb-scanner"]

    def test_rewrites_depends_on(self) -> None:
        merged = {
            "_engine": {"platform": "engine", "flows": [{"id": "site-analyzer", "order": 5}]},
            "facebook": {
                "platform": "facebook",
                "flows": [{"id": "fb-scanner", "order": 10, "depends_on": ["site-analyzer"]}],
            },
        }
        brand = {"brand": {"key": "dogfood"}}

        artifact = compose_brand_schedule_artifact(merged, brand)

        by_id = {t["id"]: t for t in artifact["tasks"]}
        assert by_id["dogfood-fb-scanner"]["depends_on"] == ["dogfood-site-analyzer"]
        # Roots get empty depends_on (rewritten from missing/empty list).
        assert by_id["dogfood-site-analyzer"]["depends_on"] == []

    def test_has_generated_header(self) -> None:
        brand = {"brand": {"key": "x"}}
        artifact = compose_brand_schedule_artifact({}, brand)

        assert artifact["_generated"] == _GENERATED_BY_BRAND_SCHEDULE
        assert artifact["tasks"] == []

    def test_raises_when_brand_key_missing(self) -> None:
        with pytest.raises(ValueError, match=r"brand\.key required"):
            compose_brand_schedule_artifact({}, {"brand": {"name": "no-key"}})


class TestOwnReplyFieldMapping:
    def test_own_replies_per_day_maps_to_own_reply_action(self) -> None:
        profiles = {
            "instagram": {
                "platform": "instagram",
                "rate_limits": {"own_replies_per_day": 15},
            },
        }

        limits = build_rate_limits(profiles)

        assert limits == {"instagram:own_reply": 15}

    def test_delay_between_own_replies_maps_to_own_reply(self) -> None:
        profiles = {
            "instagram": {
                "platform": "instagram",
                "rate_limits": {"delay_between_own_replies": "5-10s random"},
            },
        }

        delays = build_delay_ranges(profiles)

        assert delays == {"instagram:own_reply": "5-10s random"}


class TestMainBrandOverlay:
    def test_brand_dir_writes_brand_schedule(self, tmp_path: Path) -> None:
        profile_dir = tmp_path / "profiles"
        _write_profile(
            profile_dir,
            "facebook.json",
            {
                "platform": "facebook",
                "rate_limits": {"comments_per_day": 5},
                "flows": [{"id": "fb-scanner", "order": 10}],
            },
        )
        brand_dir = tmp_path / "brand"
        brand_dir.mkdir()
        (brand_dir / "brand.json").write_text(json.dumps(_BRAND_FIXTURE))
        out = tmp_path / "rate_limits.json"
        sched_out = tmp_path / "schedule.json"

        rc = _main([
            "--profile-dir", str(profile_dir),
            "--rate-limits-out", str(out),
            "--schedule-out", str(sched_out),
            "--brand-dir", str(brand_dir),
        ])

        assert rc == 0
        brand_sched_path = brand_dir / "schedule.json"
        assert brand_sched_path.exists()
        brand_sched = json.loads(brand_sched_path.read_text())
        assert brand_sched["_generated"] == _GENERATED_BY_BRAND_SCHEDULE
        # Task ids should carry the brand prefix.
        task_ids = [t["id"] for t in brand_sched["tasks"]]
        assert "tbrand-fb-scanner" in task_ids
        # Engine schedule remains abstract (no prefix).
        engine_sched = json.loads(sched_out.read_text())
        engine_ids = [t["id"] for t in engine_sched["tasks"]]
        assert "fb-scanner" in engine_ids
        assert all(not tid.startswith("tbrand-") for tid in engine_ids)
        # Rate limits picked up brand override.
        rl = json.loads(out.read_text())
        assert rl["limits"].get("instagram:own_reply") == 15

    def test_brand_dir_check_detects_drift(self, tmp_path: Path) -> None:
        profile_dir = tmp_path / "profiles"
        _write_profile(
            profile_dir,
            "facebook.json",
            {
                "platform": "facebook",
                "rate_limits": {},
                "flows": [{"id": "fb-scanner", "order": 10}],
            },
        )
        brand_dir = tmp_path / "brand"
        brand_dir.mkdir()
        (brand_dir / "brand.json").write_text(json.dumps(_BRAND_FIXTURE))
        out = tmp_path / "rate_limits.json"
        sched_out = tmp_path / "schedule.json"

        # First emit everything in sync.
        assert _main([
            "--profile-dir", str(profile_dir),
            "--rate-limits-out", str(out),
            "--schedule-out", str(sched_out),
            "--brand-dir", str(brand_dir),
        ]) == 0
        # Stomp the brand schedule with a stale version.
        (brand_dir / "schedule.json").write_text(json.dumps({
            "_generated": _GENERATED_BY_BRAND_SCHEDULE,
            "tasks": [{"id": "tbrand-stale"}],
        }))

        rc = _main([
            "--check",
            "--profile-dir", str(profile_dir),
            "--rate-limits-out", str(out),
            "--schedule-out", str(sched_out),
            "--brand-dir", str(brand_dir),
        ])

        assert rc == 1
