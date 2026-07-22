# pyright: reportMissingImports=false
# ruff: noqa: S101, S603
"""Subprocess-based integration tests for ``scripts/publish_campaign.py``.

We launch the CLI in a child process so that ``lib.config.load_config()``
re-runs against the tmp BRAND_DIR we set in the environment. ``BRAND_DIR``
needs a real ``config.json`` for ``AppSettings`` validation, so we copy
the project's reference config into ``tmp_path`` and point the CLI at it.

The cron-worker and any other long-running services are NOT touched by
these tests — every action happens inside ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_BRAND_DIR = PROJECT_ROOT.parent / "persona"
REFERENCE_CONFIG = REFERENCE_BRAND_DIR / "config.json"

CLI_MODULE = "scripts.publish_campaign"

# `function == "main"` hooks are CLI entry points returning a Unix exit code
# (0 = success), matching real hooks like scripts/publish_prepared.py::main.
_NOOP_HOOK = "def main(**kwargs):\n    return 0\n"
_MARKER_HOOK_TEMPLATE = (
    "from pathlib import Path\n"
    "def main(**kwargs):\n"
    "    Path({marker!r}).write_text('done', encoding='utf-8')\n"
    "    return 0\n"
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

@pytest.fixture
def tmp_brand_dir(tmp_path: Path) -> Path:
    """Build a minimal BRAND_DIR clone with a real config.json."""
    if not REFERENCE_CONFIG.exists():
        pytest.skip(f"reference config.json not found: {REFERENCE_CONFIG}")
    brand = tmp_path / "brand"
    brand.mkdir()
    shutil.copy(REFERENCE_CONFIG, brand / "config.json")
    (brand / "campaigns").mkdir()
    (brand / "data").mkdir()
    (brand / "state").mkdir()
    (brand / "logs").mkdir()
    return brand


def _build_tmp_campaign(
    brand_dir: Path,
    name: str,
    *,
    hook_body: str = _NOOP_HOOK,
    legacy_tasks: bool = False,
) -> Path:
    """Build a campaign at ``brand_dir/campaigns/<name>`` with one custom_hook."""
    campaign_dir = brand_dir / "campaigns" / name
    campaign_dir.mkdir(parents=True)
    (campaign_dir / "ready").mkdir()
    hooks_dir = campaign_dir / "hooks"
    hooks_dir.mkdir()
    hook = hooks_dir / "noop.py"
    hook.write_text(hook_body, encoding="utf-8")

    task = {
        "type": "custom_hook",
        "script_path": str(hook),
        "function": "main",
        "params": {},
    }
    config: dict[str, object] = {"schedule": {"cron": "0 * * * *"}}
    if legacy_tasks:
        config["tasks"] = [task]
    else:
        config["publish_tasks"] = [task]
    (campaign_dir / "campaign_config.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )
    (campaign_dir / "state.json").write_text(
        json.dumps({"last_run": None, "current_task_index": 0, "history": []}),
        encoding="utf-8",
    )
    return campaign_dir


def _run_cli(
    *args: str,
    brand_dir: Path | None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if brand_dir is not None:
        env["BRAND_DIR"] = str(brand_dir)
    return subprocess.run(
        [sys.executable, "-m", CLI_MODULE, *args],
        env=env,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_help_loads(tmp_brand_dir: Path) -> None:
    """--help must succeed and document the three primary flags."""
    res = _run_cli("--help", brand_dir=tmp_brand_dir)
    assert res.returncode == 0, res.stderr
    assert "--campaign" in res.stdout
    assert "--dry-run" in res.stdout
    assert "--health-check" in res.stdout


def test_health_check_nonexistent_campaign_exits_1(tmp_brand_dir: Path) -> None:
    res = _run_cli(
        "--campaign", "nonexistent",
        "--health-check",
        brand_dir=tmp_brand_dir,
    )
    assert res.returncode == 1
    assert "CAMPAIGN_BROKEN" in res.stderr or "CAMPAIGN_NOT_FOUND" in res.stderr


def test_health_check_valid_campaign_exits_0(tmp_brand_dir: Path) -> None:
    _build_tmp_campaign(tmp_brand_dir, "test-campaign")
    res = _run_cli(
        "--campaign", "test-campaign",
        "--health-check",
        brand_dir=tmp_brand_dir,
    )
    assert res.returncode == 0, res.stderr
    assert "Campaign OK" in res.stdout


def test_publish_dry_run_no_state_mutation(tmp_brand_dir: Path) -> None:
    campaign_dir = _build_tmp_campaign(tmp_brand_dir, "test-campaign")
    state_file = campaign_dir / "state.json"
    mtime_before = state_file.stat().st_mtime_ns
    bytes_before = state_file.read_bytes()

    res = _run_cli(
        "--campaign", "test-campaign",
        "--dry-run",
        brand_dir=tmp_brand_dir,
    )

    assert res.returncode == 0, res.stderr
    assert state_file.stat().st_mtime_ns == mtime_before
    assert state_file.read_bytes() == bytes_before
    assert not (campaign_dir / "worker.lock").exists()


def test_publish_executes_hook(tmp_brand_dir: Path, tmp_path: Path) -> None:
    """A real publish run executes the hook and updates state.json."""
    marker = tmp_path / "cli_marker.txt"
    hook_body = _MARKER_HOOK_TEMPLATE.format(marker=str(marker))
    campaign_dir = _build_tmp_campaign(
        tmp_brand_dir,
        "test-campaign",
        hook_body=hook_body,
    )

    res = _run_cli(
        "--campaign", "test-campaign",
        brand_dir=tmp_brand_dir,
    )

    assert res.returncode == 0, res.stderr
    assert marker.exists(), "hook did not run"
    assert marker.read_text(encoding="utf-8") == "done"

    state = json.loads((campaign_dir / "state.json").read_text(encoding="utf-8"))
    assert state["last_run"] is not None
    assert state["current_task_index"] == 0  # reset after success
    assert len(state["history"]) == 1
    entry = state["history"][-1]
    assert entry["status"] == "success"
    assert entry["stage"] == "publish"
