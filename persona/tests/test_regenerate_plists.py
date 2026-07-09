# pyright: reportMissingImports=false
"""Tests for scripts/regenerate_plists.py + scripts/_cron_to_launchd.py."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure the repo root is importable so `from scripts...` and `from api...` work.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from api.schedule_config import ScheduleTask
from scripts._cron_to_launchd import UnsupportedCronError, cron_to_launchd
from scripts.regenerate_plists import build_plist, diff_plist


def _check(condition: bool, msg: str = "assertion failed") -> None:
    """Pytest-compatible assertion wrapper (avoids bare `assert` for linters
    that flag S101 outside test paths)."""
    if not condition:
        pytest.fail(msg)


# ---------------------------------------------------------------------------
# cron_to_launchd
# ---------------------------------------------------------------------------


def test_cron_daily_simple() -> None:
    _check(cron_to_launchd("0 15 * * *") == {"Hour": 15, "Minute": 0})


def test_cron_with_minute() -> None:
    _check(cron_to_launchd("30 15 * * *") == {"Hour": 15, "Minute": 30})


def test_cron_monthly() -> None:
    _check(cron_to_launchd("0 15 1 * *") == {"Day": 1, "Hour": 15, "Minute": 0})


def test_cron_weekly() -> None:
    _check(cron_to_launchd("0 15 * * 0") == {"Weekday": 0, "Hour": 15, "Minute": 0})


def test_cron_hourly_range() -> None:
    result = cron_to_launchd("0 8-22 * * *")
    _check(isinstance(result, list))
    _check(len(result) == 15)
    _check(result[0] == {"Hour": 8, "Minute": 0})
    _check(result[-1] == {"Hour": 22, "Minute": 0})


def test_cron_hourly_list() -> None:
    result = cron_to_launchd("0 8,12,18 * * *")
    _check(isinstance(result, list))
    _check(len(result) == 3)
    _check([d["Hour"] for d in result] == [8, 12, 18])
    _check(all(d["Minute"] == 0 for d in result))


def test_cron_unsupported_step_raises() -> None:
    with pytest.raises(UnsupportedCronError):
        cron_to_launchd("*/5 * * * *")


# ---------------------------------------------------------------------------
# build_plist
# ---------------------------------------------------------------------------


def _fake_task(
    *,
    task_id: str = "dogfood-test-task",
    skill: str | None = None,
    script: str | None = None,
    requires_browser: bool = False,
    cron: str = "0 10 * * *",
    timeout_seconds: int | None = None,
    script_args: list[str] | None = None,
) -> ScheduleTask:
    """Build a ScheduleTask via model_extra (the schema allows extra fields)."""
    raw: dict[str, Any] = {
        "id": task_id,
        "skill": skill,
        "script": script,
        "requires_browser": requires_browser,
        "schedule": {"cron": cron},
    }
    if timeout_seconds is not None:
        raw["timeout_seconds"] = timeout_seconds
    if script_args is not None:
        raw["script_args"] = script_args
    return ScheduleTask(**raw)


def test_plist_script_no_browser() -> None:
    task = _fake_task(
        task_id="dogfood-content-ideator",
        skill=None,
        script="scripts/x.py",
        requires_browser=False,
        cron="0 10 * * 0",
    )
    plist = build_plist(task, python3="/usr/bin/python3", claude_bin=None)
    args = plist["ProgramArguments"]
    _check("scripts/run_with_watchdog.py" not in args)
    _check(args[0] == "/usr/bin/python3")
    _check(args[1] == "scripts/x.py")
    _check(plist["Label"] == "com.persona.content-ideator")
    _check(plist["StartCalendarInterval"] == {"Weekday": 0, "Hour": 10, "Minute": 0})


def test_plist_script_with_browser() -> None:
    task = _fake_task(
        task_id="dogfood-fb-scanner",
        skill=None,
        script="scripts/x.py",
        requires_browser=True,
        cron="0 12 * * *",
    )
    plist = build_plist(task, python3="/usr/bin/python3", claude_bin=None)
    args = plist["ProgramArguments"]
    _check(args == [
        "/usr/bin/python3",
        "scripts/run_with_watchdog.py",
        "scripts/x.py",
        "--timeout",
        "300",
    ])


def test_plist_skill_only() -> None:
    task = _fake_task(
        task_id="dogfood-site-analyzer",
        skill="site-analyzer",
        script=None,
        requires_browser=False,
        cron="0 15 * * *",
    )
    plist = build_plist(
        task, python3="/usr/bin/python3", claude_bin="/usr/local/bin/claude",
    )
    _check(plist["ProgramArguments"] == [
        "/usr/local/bin/claude",
        "--dangerously-skip-permissions",
        "/site-analyzer",
    ])
    _check(plist["Label"] == "com.persona.site-analyzer")
    env = plist["EnvironmentVariables"]
    _check(env["PYTHONUNBUFFERED"] == "1")
    _check("PATH" in env)
    _check("HOME" in env)
    _check("BRAND_DIR" in env)


# ---------------------------------------------------------------------------
# diff_plist
# ---------------------------------------------------------------------------


def _sample_plist() -> dict[str, Any]:
    return {
        "Label": "com.persona.test",
        "ProgramArguments": ["/usr/bin/python3", "scripts/x.py"],
        "StartCalendarInterval": {"Hour": 10, "Minute": 0},
    }


def test_diff_create() -> None:
    action, _reason = diff_plist("com.persona.test", _sample_plist(), None)
    _check(action == "CREATE")


def test_diff_no_change() -> None:
    proposed = _sample_plist()
    existing = _sample_plist()
    action, _reason = diff_plist("com.persona.test", proposed, existing)
    _check(action == "OK")


def test_diff_update() -> None:
    proposed = _sample_plist()
    existing = _sample_plist()
    existing["StartCalendarInterval"] = {"Hour": 11, "Minute": 0}
    action, reason = diff_plist("com.persona.test", proposed, existing)
    _check(action == "UPDATE")
    _check("StartCalendarInterval" in reason)
