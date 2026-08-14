"""Pin the two cron flows that share `crewai_social_posts_pipeline.py`.

The script has three phases and one process; which of them run is decided
entirely by a CLI flag. That makes the flow definitions load-bearing in a way
a plain "does the script work" test can't see:

  * `social-posts-compose` (daily, `--compose-only`) fills the review queue.
    Without it nothing ever composes and the Social Posts page stays empty
    forever -- the state this flow was added to fix.
  * `social-posts-release` (hourly, `--release-only`) publishes what review
    approved.

They must stay DISJOINT. `worker_wp_ideas_social_post`'s status guard is a
read-then-check, not an atomic claim, so two sweeps overlapping on the same
due row could both publish it -- an argless (all-three-phases) compose flow
whose run outlasts the top of the hour is exactly that overlap.

Assertions read `data/schedule.json`, not `profiles/_engine.json`: the
profile is gitignored (`.gitignore:76`, brand-specific description text) so
it may be absent in CI, while the generated schedule is committed and is what
`backfill_brand_registration` seeds `schedule_tasks` from. The one test that
does look at the profile skips itself when the file isn't there.
"""
# ruff: noqa: S101  (pytest tests use `assert` by design)

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_APP_ROOT = Path(__file__).resolve().parent.parent
_ENGINE_PROFILE = _APP_ROOT / "profiles" / "_engine.json"
_GENERATED_SCHEDULE = _APP_ROOT / "data" / "schedule.json"
_PIPELINE = _APP_ROOT / "scripts" / "crewai_social_posts_pipeline.py"


def _entries(path: Path, key: str) -> dict[str, dict[str, Any]]:
    return {f["id"]: f for f in json.loads(path.read_text(encoding="utf-8"))[key]}


def _tasks() -> dict[str, dict[str, Any]]:
    return _entries(_GENERATED_SCHEDULE, "tasks")


def test_both_social_post_flows_exist() -> None:
    tasks = _tasks()
    assert "social-posts-compose" in tasks
    assert "social-posts-release" in tasks


def test_the_two_flows_are_disjoint_modes() -> None:
    """Neither flow may run argless: that mode does compose AND release."""
    tasks = _tasks()
    assert tasks["social-posts-compose"]["args"] == ["--compose-only"]
    assert tasks["social-posts-release"]["args"] == ["--release-only"]


def test_compose_runs_daily_and_release_hourly() -> None:
    """Composition is LLM + image work per candidate, so it runs once a day;
    release is cheap and runs hourly so an approved post never waits long
    past its slot."""
    tasks = _tasks()
    compose_cron = tasks["social-posts-compose"]["schedule"]["cron"].split()
    assert compose_cron[1:] == ["16", "*", "*", "*"], compose_cron
    assert tasks["social-posts-release"]["schedule"]["cron"] == "5 * * * *"


def test_compose_outlives_the_default_worker_timeout() -> None:
    """`task_worker`'s default is 10 minutes; a crew run plus a Gemini image
    per candidate blows past that and would be killed mid-compose."""
    assert _tasks()["social-posts-compose"]["timeout_minutes"] >= 20


def test_compose_needs_no_browser_and_precedes_release() -> None:
    tasks = _tasks()
    assert tasks["social-posts-compose"]["requires_browser"] is False
    assert tasks["social-posts-compose"]["order"] < tasks["social-posts-release"]["order"]


def test_generated_schedule_matches_the_profile() -> None:
    """`data/schedule.json` is generated from the profiles by
    `tools.profiles_build`; a profile edit that never got rebuilt leaves the
    committed schedule -- and therefore every seeded `schedule_tasks` row --
    stale."""
    if not _ENGINE_PROFILE.is_file():
        pytest.skip(f"{_ENGINE_PROFILE.name} is gitignored and absent here")
    profile = _entries(_ENGINE_PROFILE, "flows")["social-posts-compose"]
    task = _tasks()["social-posts-compose"]
    assert task["schedule"] == profile["schedule"]
    assert task["args"] == profile["args"]


def test_cli_refuses_both_modes_at_once() -> None:
    """The flags are mutually exclusive at the argparse level, so a flow that
    asked for both fails loudly on its first run instead of silently picking
    one."""
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(_PIPELINE), "--compose-only", "--release-only"],
        cwd=_APP_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 2, result.stderr
    assert "not allowed with argument" in result.stderr
