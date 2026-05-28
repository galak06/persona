# pyright: reportMissingImports=false
# ruff: noqa: S101
"""Tests for lib.campaigns.runner.run_campaign.

Each test builds a self-contained fake campaign under ``tmp_path`` so no
real ``BRAND_DIR`` is required. Hook scripts are tiny python files written
into ``tmp_path/hooks/`` and referenced by absolute path in
``campaign_config.json``.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.campaigns import CampaignRunResult, LockHeldError, run_campaign

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_NOOP_HOOK = "def main(**kwargs):\n    return True\n"
_FAIL_HOOK = "def main(**kwargs):\n    return False\n"
_SLEEP_HOOK_TEMPLATE = (
    "import time\n"
    "def main(**kwargs):\n"
    "    time.sleep({sleep_seconds})\n"
    "    return True\n"
)


def _write_hook(hooks_dir: Path, name: str, body: str) -> Path:
    hooks_dir.mkdir(parents=True, exist_ok=True)
    script = hooks_dir / f"{name}.py"
    script.write_text(body, encoding="utf-8")
    return script


def _build_campaign(
    campaign_dir: Path,
    *,
    publish_tasks: list[dict[str, Any]] | None = None,
    prepare_tasks: list[dict[str, Any]] | None = None,
    legacy_tasks: list[dict[str, Any]] | None = None,
) -> None:
    campaign_dir.mkdir(parents=True, exist_ok=True)
    (campaign_dir / "ready").mkdir(exist_ok=True)
    config: dict[str, Any] = {"schedule": {"cron": "0 * * * *"}}
    if publish_tasks is not None:
        config["publish_tasks"] = publish_tasks
    if prepare_tasks is not None:
        config["prepare_tasks"] = prepare_tasks
    if legacy_tasks is not None:
        config["tasks"] = legacy_tasks
    (campaign_dir / "campaign_config.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )
    (campaign_dir / "state.json").write_text(
        json.dumps({"last_run": None, "current_task_index": 0, "history": []}),
        encoding="utf-8",
    )


def _hook_task(hook: Path) -> dict[str, Any]:
    return {
        "type": "custom_hook",
        "script_path": str(hook),
        "function": "main",
        "params": {},
    }


def _read_state(campaign_dir: Path) -> dict[str, Any]:
    raw = (campaign_dir / "state.json").read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(raw)
    return data


# --------------------------------------------------------------------------- #
# Test A — successful no-op run, no ready/ to promote
# --------------------------------------------------------------------------- #

def test_run_publish_no_op_task_succeeds(tmp_path: Path) -> None:
    hook = _write_hook(tmp_path / "hooks", "noop", _NOOP_HOOK)
    campaign_dir = tmp_path / "campaigns" / "test-A"
    _build_campaign(campaign_dir, publish_tasks=[_hook_task(hook)])

    result = run_campaign(campaign_dir, stage="publish")

    assert isinstance(result, CampaignRunResult)
    assert result.ok is True
    assert result.tasks_run == 1
    assert result.error is None
    assert result.published_folder is None  # empty ready/ — no promotion

    state = _read_state(campaign_dir)
    assert state["current_task_index"] == 0  # reset after success
    assert state["last_run"] is not None and isinstance(state["last_run"], str)
    assert len(state["history"]) == 1
    entry = state["history"][-1]
    assert entry["status"] == "success"
    assert entry["stage"] == "publish"


# --------------------------------------------------------------------------- #
# Test B — promotes ready/ contents into published/<ts>/
# --------------------------------------------------------------------------- #

def test_run_publish_promotes_ready_to_published(tmp_path: Path) -> None:
    hook = _write_hook(tmp_path / "hooks", "noop", _NOOP_HOOK)
    campaign_dir = tmp_path / "campaigns" / "test-B"
    _build_campaign(campaign_dir, publish_tasks=[_hook_task(hook)])
    marker = campaign_dir / "ready" / "before-promotion.txt"
    marker.write_text("hello", encoding="utf-8")

    result = run_campaign(campaign_dir, stage="publish")

    assert result.ok is True
    assert result.published_folder is not None
    promoted = (
        campaign_dir / "published" / result.published_folder / "before-promotion.txt"
    )
    assert promoted.exists()
    assert promoted.read_text(encoding="utf-8") == "hello"
    assert list((campaign_dir / "ready").iterdir()) == []  # swept

    entry = _read_state(campaign_dir)["history"][-1]
    assert entry["published_folder"] == result.published_folder


# --------------------------------------------------------------------------- #
# Test C — failed task persists current_task_index for resume
# --------------------------------------------------------------------------- #

def test_run_publish_failed_task_persists_resume_index(tmp_path: Path) -> None:
    hook = _write_hook(tmp_path / "hooks", "fail", _FAIL_HOOK)
    campaign_dir = tmp_path / "campaigns" / "test-C"
    _build_campaign(campaign_dir, publish_tasks=[_hook_task(hook)])

    result = run_campaign(campaign_dir, stage="publish")

    assert result.ok is False
    assert result.error is not None
    assert "falsy" in result.error

    state = _read_state(campaign_dir)
    assert state["current_task_index"] == 0  # NOT advanced — resume from here
    assert len(state["history"]) == 1
    entry = state["history"][-1]
    assert entry["status"] == "error"
    assert entry["stage"] == "publish"
    assert entry["failed_at_task"] == 0


# --------------------------------------------------------------------------- #
# Test D — dry-run plans tasks but mutates nothing
# --------------------------------------------------------------------------- #

def test_dry_run_no_mutation(tmp_path: Path) -> None:
    hook = _write_hook(tmp_path / "hooks", "noop", _NOOP_HOOK)
    campaign_dir = tmp_path / "campaigns" / "test-D"
    _build_campaign(campaign_dir, publish_tasks=[_hook_task(hook)])
    state_file = campaign_dir / "state.json"
    mtime_before = state_file.stat().st_mtime_ns
    state_before = state_file.read_text(encoding="utf-8")

    result = run_campaign(campaign_dir, stage="publish", dry_run=True)

    assert result.ok is True
    # tasks_run reports the count of tasks PLANNED in dry-run mode (matches runner)
    assert result.tasks_run == 1
    assert result.error is None
    assert result.published_folder is None
    assert state_file.stat().st_mtime_ns == mtime_before
    assert state_file.read_text(encoding="utf-8") == state_before
    assert not (campaign_dir / "published").exists()
    assert not (campaign_dir / "worker.lock").exists()


# --------------------------------------------------------------------------- #
# Test E — LockHeldError when another process holds worker.lock
# --------------------------------------------------------------------------- #

def _hold_lock_worker(campaign_dir_str: str, project_root_str: str) -> None:
    """Subprocess entry: import path setup + call run_campaign with a sleep hook."""
    import sys as _sys

    _sys.path.insert(0, project_root_str)
    _sys.path.insert(0, str(Path(project_root_str) / "lib"))
    from lib.campaigns import run_campaign as _run

    _run(Path(campaign_dir_str), stage="publish")


def test_lock_held_raises(tmp_path: Path) -> None:
    """Spawn a process whose hook sleeps long enough for us to race in."""
    sleep_hook = _write_hook(
        tmp_path / "hooks",
        "sleep",
        _SLEEP_HOOK_TEMPLATE.format(sleep_seconds=3),
    )
    campaign_dir = tmp_path / "campaigns" / "test-E"
    _build_campaign(campaign_dir, publish_tasks=[_hook_task(sleep_hook)])

    ctx = mp.get_context("spawn")
    proc = ctx.Process(
        target=_hold_lock_worker,
        args=(str(campaign_dir), str(PROJECT_ROOT)),
    )
    proc.start()
    try:
        # Poll for the lock file to appear (the child has acquired flock).
        deadline = time.monotonic() + 5.0
        lock_file = campaign_dir / "worker.lock"
        while time.monotonic() < deadline and not lock_file.exists():
            time.sleep(0.05)
        assert lock_file.exists(), "child never acquired the lock"

        with pytest.raises(LockHeldError):
            run_campaign(campaign_dir, stage="publish")
    finally:
        proc.join(timeout=10)
        assert proc.exitcode == 0


# --------------------------------------------------------------------------- #
# Test F — empty prepare stage returns ok without recording history
# --------------------------------------------------------------------------- #

def test_prepare_stage_empty_returns_ok(tmp_path: Path) -> None:
    campaign_dir = tmp_path / "campaigns" / "test-F"
    _build_campaign(campaign_dir, publish_tasks=[], prepare_tasks=[])

    result = run_campaign(campaign_dir, stage="prepare")

    assert result.ok is True
    assert result.tasks_run == 0
    assert result.error is None
    # Early-return path: state.json is untouched (no history entry).
    assert _read_state(campaign_dir)["history"] == []


# --------------------------------------------------------------------------- #
# Test G — legacy `tasks` field is migrated by validator and runs as publish
# --------------------------------------------------------------------------- #

def test_legacy_tasks_field_runs_as_publish(tmp_path: Path) -> None:
    hook = _write_hook(tmp_path / "hooks", "noop_legacy", _NOOP_HOOK)
    campaign_dir = tmp_path / "campaigns" / "test-G"
    _build_campaign(campaign_dir, legacy_tasks=[_hook_task(hook)])

    result = run_campaign(campaign_dir, stage="publish")

    assert result.ok is True
    assert result.tasks_run == 1
    state = _read_state(campaign_dir)
    assert state["history"][-1]["status"] == "success"
