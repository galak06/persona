#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""regenerate_plists.py — read schedule.json, emit launchd plists.

Single source of truth: ``persona/schedule.json``. Reads tasks,
builds proposed launchd plists, compares semantically against on-disk
plists in ``~/Library/LaunchAgents``, prints a drift table. ``--apply``
writes changed plists atomically; ``--bootstrap`` reloads via launchctl.
Reuses the parser + label-log map from the ``api`` package.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Make `api.*` and `lib.*` importable when run as `python3 scripts/...`.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from api.schedule_config import load_schedule_config
from api.schedule_state import _LABEL_TO_LOG
from scripts._cron_to_launchd import UnsupportedCronError, cron_to_launchd

REPO_ROOT = _REPO_ROOT
_BRAND_LABEL_PREFIX = "com.persona."
_TASK_ID_PREFIX = "dogfood-"
_DEFAULT_TIMEOUT = 300


# ---------------------------------------------------------------------------
# Plist construction
# ---------------------------------------------------------------------------

def _task_extra(task: Any) -> dict[str, Any]:
    return task.model_extra or {}


def _resolve_log_path(suffix: str) -> str:
    log_name = _LABEL_TO_LOG.get(suffix, f"cron_{suffix.replace('-', '_')}.log")
    brand_dir = Path(_resolve_brand_dir())
    return str(brand_dir / "logs" / log_name)


def _resolve_brand_dir() -> str:
    """Best-effort absolute path to the brand dir for the EnvironmentVariables block."""
    env = os.environ.get("BRAND_DIR")
    if env:
        return str(Path(env).resolve())
    # Fallback: sibling directory ../persona.
    fallback = (REPO_ROOT.parent / "persona").resolve()
    return str(fallback)


def _split_script(script: str) -> list[str]:
    """Shell-split a schedule ``script`` string into argv tokens.

    The schedule stores invocations as a single string that may embed CLI
    flags (``scripts/content_pipeline.py --stage publish``) or a module
    invocation (``python -m ideator.main``). launchd's ProgramArguments wants
    each token as its own element, so split with ``shlex`` semantics.

    A leading interpreter token (``python``/``python3``) is dropped because the
    plist already supplies the absolute venv interpreter as argv[0]; the
    remaining ``-m module ...`` (or bare script + args) tokens are appended
    after it.
    """
    tokens = shlex.split(script.strip())
    if tokens and tokens[0] in {"python", "python3"}:
        tokens = tokens[1:]
    return tokens


def build_plist(
    task: Any,
    *,
    python3: str,
    claude_bin: str | None,
) -> dict[str, Any]:
    """Build the proposed plist dict for one ScheduleTask.

    Raises UnsupportedCronError on bad cron. Returns ``{}`` for tasks the
    caller should skip (no script, no skill).
    """
    extra = _task_extra(task)
    schedule = extra.get("schedule") or {}
    cron = schedule.get("cron") if isinstance(schedule, dict) else None
    if not cron:
        raise UnsupportedCronError(f"task {task.id} has no schedule.cron")

    suffix = task.id.removeprefix(_TASK_ID_PREFIX)
    label = f"{_BRAND_LABEL_PREFIX}{suffix}"

    script = extra.get("script")
    requires_browser = bool(extra.get("requires_browser"))
    timeout_seconds = int(extra.get("timeout_seconds") or _DEFAULT_TIMEOUT)
    script_args = extra.get("script_args") or []

    if script:
        script_tokens = _split_script(script)
        if requires_browser:
            program_args = [python3, "scripts/run_with_watchdog.py", *script_tokens,
                            "--timeout", str(timeout_seconds)]
        else:
            program_args = [python3, *script_tokens, *[str(a) for a in script_args]]
    elif task.skill:
        if not claude_bin:
            raise SystemExit(
                "claude CLI not found; pass --claude-bin <path> or install claude"
            )
        program_args = [claude_bin, "--dangerously-skip-permissions", f"/{task.skill}"]
    else:
        return {}  # signal: skip

    calendar = cron_to_launchd(cron)
    log_path = _resolve_log_path(suffix)

    working_dir_rel = extra.get("working_directory")
    working_directory = str(REPO_ROOT / working_dir_rel) if working_dir_rel else str(REPO_ROOT)

    plist: dict[str, Any] = {
        "Label": label,
        "ProgramArguments": program_args,
        "WorkingDirectory": working_directory,
        "StartCalendarInterval": calendar,
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONUNBUFFERED": "1",
            "HOME": os.environ.get("HOME", str(Path.home())),
            "BRAND_DIR": _resolve_brand_dir(),
        },
    }
    return plist


# ---------------------------------------------------------------------------
# Diff / write / bootstrap
# ---------------------------------------------------------------------------

def diff_plist(
    label: str,
    proposed: dict[str, Any],
    existing: dict[str, Any] | None,
) -> tuple[str, str]:
    """Return ``(action, reason)`` for one label.

    Actions: ``CREATE`` (no existing), ``OK`` (identical), ``UPDATE`` (drift),
    ``SKIP`` (proposed is empty / unbuildable).
    """
    if not proposed:
        return ("SKIP", "no script and no skill")
    if existing is None:
        return ("CREATE", "new plist")
    diff_keys: list[str] = []
    for key in sorted(set(proposed) | set(existing)):
        if proposed.get(key) != existing.get(key):
            diff_keys.append(key)
    if not diff_keys:
        return ("OK", "matches on-disk")
    return ("UPDATE", "changed: " + ", ".join(diff_keys))


def write_plist(path: Path, content: dict[str, Any]) -> None:
    """Atomically write ``content`` as XML plist to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as fp:
        plistlib.dump(content, fp, sort_keys=False)
    os.replace(tmp, path)


def bootstrap(label: str, plist_path: Path) -> tuple[bool, str]:
    """Run ``launchctl bootout`` (ignored) + ``bootstrap`` (checked).

    Returns ``(ok, message)``. Never raises.
    """
    uid = os.getuid()
    target = f"gui/{uid}"
    bootout_cmd = ["/bin/launchctl", "bootout", target, str(plist_path)]
    bootstrap_cmd = ["/bin/launchctl", "bootstrap", target, str(plist_path)]
    runner = subprocess.run
    try:
        runner(bootout_cmd, capture_output=True, text=True, check=False, timeout=15)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return (False, f"bootout failed: {exc}")
    try:
        result = runner(
            bootstrap_cmd, capture_output=True, text=True, check=False, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return (False, f"bootstrap failed: {exc}")
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()[:120]
        return (False, f"bootstrap rc={result.returncode}: {msg}")
    return (True, f"reloaded {label}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_existing(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("rb") as fp:
            data = plistlib.load(fp)
        return data if isinstance(data, dict) else None
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--diff", action="store_true", help="print drift table (default)")
    p.add_argument("--dry-run", action="store_true", help="print proposed plists, no write")
    p.add_argument("--apply", action="store_true", help="write changed plists atomically")
    p.add_argument("--bootstrap", action="store_true",
                   help="after --apply, launchctl bootout/bootstrap changed plists")
    p.add_argument("--only", help="restrict to a single label suffix (e.g. fb-scanner)")
    p.add_argument("--claude-bin", help="path to the claude CLI binary")
    p.add_argument("--python", help="python3 binary to embed in plists")
    p.add_argument("--strict", action="store_true",
                   help="--diff exits 1 if any drift detected (CI gate)")
    return p.parse_args(argv)


def _resolve_claude_bin(cli_value: str | None) -> str | None:
    return cli_value or os.environ.get("CLAUDE_BIN") or shutil.which("claude")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not (args.diff or args.dry_run or args.apply):
        args.diff = True  # default mode

    python3 = args.python or sys.executable
    claude_bin = _resolve_claude_bin(args.claude_bin)

    config = load_schedule_config()
    if not config.tasks:
        sys.stderr.write("schedule.json has no tasks\n")
        return 1

    launch_dir = Path.home() / "Library" / "LaunchAgents"
    proposals: dict[str, tuple[Path, dict[str, Any], dict[str, Any] | None]] = {}
    skipped: list[tuple[str, str]] = []

    for task in config.tasks:
        if not task.id.startswith(_TASK_ID_PREFIX):
            skipped.append((task.id, "id does not start with 'dogfood-'"))
            continue
        suffix = task.id.removeprefix(_TASK_ID_PREFIX)
        if args.only and suffix != args.only:
            continue
        label = f"{_BRAND_LABEL_PREFIX}{suffix}"
        try:
            proposed = build_plist(task, python3=python3, claude_bin=claude_bin)
        except UnsupportedCronError as exc:
            skipped.append((label, f"cron: {exc}"))
            continue
        if not proposed:
            skipped.append((label, "no script and no skill"))
            continue
        path = launch_dir / f"{label}.plist"
        existing = _load_existing(path)
        proposals[label] = (path, proposed, existing)

    _emit = sys.stdout.write
    # Drift table
    _emit(f"{'label':<50} {'action':<8} reason\n")
    _emit(("-" * 90) + "\n")
    drift_count = 0
    for label, (_path, proposed, existing) in sorted(proposals.items()):
        action, reason = diff_plist(label, proposed, existing)
        if action in ("CREATE", "UPDATE"):
            drift_count += 1
        _emit(f"{label:<50} {action:<8} {reason}\n")
    for label, reason in skipped:
        _emit(f"{label:<50} {'SKIP':<8} {reason}\n")

    # Orphan check: any plist on disk we didn't propose for.
    if launch_dir.exists():
        on_disk = {p.stem for p in launch_dir.glob(f"{_BRAND_LABEL_PREFIX}*.plist")}
        for label in sorted(on_disk - set(proposals)):
            if args.only:
                continue
            _emit(f"WARN: Orphaned plist: {label} (not in schedule.json)\n")

    if args.dry_run:
        _emit("\n--- proposed plists ---\n")
        for label, (_path, proposed, _existing) in sorted(proposals.items()):
            _emit(f"\n# {label}\n")
            _emit(plistlib.dumps(proposed, sort_keys=False).decode("utf-8") + "\n")

    if args.apply:
        for label, (path, proposed, existing) in sorted(proposals.items()):
            action, _reason = diff_plist(label, proposed, existing)
            if action not in ("CREATE", "UPDATE"):
                continue
            write_plist(path, proposed)
            _emit(f"WROTE {label} -> {path}\n")
            if args.bootstrap:
                ok, msg = bootstrap(label, path)
                _emit(f"  bootstrap: {'ok' if ok else 'FAIL'} — {msg}\n")

    if args.strict and args.diff and drift_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
