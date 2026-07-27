"""launchd plist generation + install/uninstall for brand schedules.

Slice D companion to ``tools.profiles_build``. Kept in its own module so the
build orchestrator stays focused on artifact composition while plist details
(host PATH, watchdog wrapper conventions, ``launchctl`` flags) live here.

Public surface (consumed by ``profiles_build``):
    cron_to_launchd(cron) -> dict | list[dict]
    compose_plist_xml(task, brand, paths) -> bytes
    compose_brand_plists(merged_profiles, brand, paths=None) -> dict[str, bytes]
    write_plist_dir(plists, plist_dir) -> list[Path]
    check_plist_dir(plists, plist_dir) -> list[str]
    install_plists(plist_dir, apply, ...) -> int
    uninstall_plists(brand, apply, ...) -> int
    default_plist_paths() -> PlistPaths
    resolve_plist_paths(brand_dir) -> PlistPaths
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

Flow = dict[str, Any]
Profile = dict[str, Any]
PlistPaths = dict[str, str]
PlistDict = dict[str, Any]

_ROOT = Path(__file__).resolve().parent.parent

# Mirror the PATH baked into installed plists so Playwright + brew tools
# resolve at launchd-spawn time (no shell init runs).
# Override via PERSONA_LAUNCHD_PATH env var, or let this default be used.
_HOME = str(Path.home())
_DEFAULT_LAUNCHD_PATH: str = os.environ.get("PERSONA_LAUNCHD_PATH", (
    f"{_HOME}/.local/bin:"
    "/opt/homebrew/Caskroom/miniconda/base/condabin:"
    f"{_HOME}/bin:"
    "/usr/local/bin:"
    f"{_HOME}/.pyenv/shims:"
    "/opt/homebrew/bin:"
    "/opt/homebrew/sbin:"
    "/System/Cryptexes/App/usr/bin:"
    "/usr/bin:/bin:/usr/sbin:/sbin:"
    "/var/run/com.apple.security.cryptexd/codex.system/bootstrap/usr/local/bin:"
    "/var/run/com.apple.security.cryptexd/codex.system/bootstrap/usr/bin:"
    "/var/run/com.apple.security.cryptexd/codex.system/bootstrap/usr/appleinternal/bin"
))
_DEFAULT_VENV_PYTHON: str = str(_ROOT / ".venv" / "bin" / "python")
_DEFAULT_WORKING_DIR: str = str(_ROOT)
_DEFAULT_CLAUDE_CLI: str = str(Path.home() / ".local" / "bin" / "claude")
_WATCHDOG_SCRIPT: str = "scripts/run_with_watchdog.py"
_WATCHDOG_TIMEOUT: str = "300"
LAUNCH_AGENTS_DIR: Path = Path.home() / "Library" / "LaunchAgents"


def default_plist_paths() -> PlistPaths:
    """Path conventions for plist generation. Override per-test via injection."""
    return {
        "python": _DEFAULT_VENV_PYTHON,
        "claude_cli": _DEFAULT_CLAUDE_CLI,
        "working_dir": _DEFAULT_WORKING_DIR,
        "launchd_path": _DEFAULT_LAUNCHD_PATH,
        "home": str(Path.home()),
        "watchdog_script": _WATCHDOG_SCRIPT,
        "watchdog_timeout": _WATCHDOG_TIMEOUT,
    }


def resolve_plist_paths(brand_dir: Path) -> PlistPaths:
    """Build the plist-path conventions for a given brand_dir."""
    paths = default_plist_paths()
    paths["brand_dir"] = str(brand_dir)
    paths["log_dir"] = str(brand_dir / "logs")
    return paths


def _expand_hour_field(hour_field: str) -> list[int]:
    """Expand a cron hour field into a sorted list of int hours.

    Supports: "*" (all 24), "9" (single), "9,14,20" (csv), "8-22" (range).
    Lists with overlapping ranges/values are de-duped + sorted.
    """
    if hour_field == "*":
        return list(range(24))
    hours: set[int] = set()
    for part in hour_field.split(","):
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if lo > hi or lo < 0 or hi > 23:
                raise ValueError(f"Invalid hour range: {part}")
            hours.update(range(lo, hi + 1))
        elif "/" in part:
            raise NotImplementedError(
                f"Cron step values ('{part}') are not supported by cron_to_launchd"
            )
        else:
            val = int(part)
            if val < 0 or val > 23:
                raise ValueError(f"Invalid hour: {val}")
            hours.add(val)
    return sorted(hours)


def cron_to_launchd(cron: str) -> dict[str, int] | list[dict[str, int]]:
    """Translate a 5-field POSIX cron string to launchd StartCalendarInterval.

    Field order: ``min hour day-of-month month day-of-week``.
    Returns a single dict for the simple cases and a list-of-dicts when the
    hour field expands (csv or range) — matching the convention used by the
    plists already installed under ~/Library/LaunchAgents/.

    Step values (``*/N``) are not supported — none of the brand schedules
    use them today; raise NotImplementedError so it's caught at build time.

    Cron and launchd agree on weekday numbering (0 = Sunday).
    """
    fields = cron.strip().split()
    if len(fields) != 5:
        raise ValueError(
            f"Expected 5-field cron 'min hour day month weekday', got {len(fields)} "
            f"in {cron!r}"
        )
    minute_s, hour_s, dom_s, month_s, dow_s = fields
    if "/" in minute_s or "/" in dow_s or "/" in dom_s or "/" in month_s:
        raise NotImplementedError(f"Cron step values not supported in {cron!r}")
    if dom_s != "*" or month_s != "*":
        raise NotImplementedError(
            f"day-of-month/month constraints not supported by cron_to_launchd: {cron!r}"
        )
    minute = int(minute_s)
    hours = _expand_hour_field(hour_s)
    weekday: int | None = None if dow_s == "*" else int(dow_s)

    def _entry(hour: int) -> dict[str, int]:
        out: dict[str, int] = {"Hour": hour, "Minute": minute}
        if weekday is not None:
            out["Weekday"] = weekday
        return out

    if len(hours) == 1:
        return _entry(hours[0])
    return [_entry(h) for h in hours]


def _task_short_name(task_id: str, brand_key: str) -> str:
    """Strip the `<brand-key>-` prefix from a task id to get the bare skill slug.

    `dogfood-fb-scanner` + brand key `dogfood` → `fb-scanner`.
    Falls back to the raw id if the prefix doesn't match.
    """
    prefix = f"{brand_key}-"
    if task_id.startswith(prefix):
        return task_id[len(prefix):]
    return task_id


def _log_basename(task: Flow) -> str:
    """Derive the host log-file basename `<basename>.log`.

    Mirrors the existing convention: take the script's filename stem (drop the
    `scripts/` dir + `.py`/CLI args), prefix with `cron_`. For tasks where the
    script is invoked as `python -m <module>` (or any non-path interpreter
    invocation), fall back to the skill name so the log file is named after
    the logical job, not the interpreter binary. Same fallback applies when
    `task.script` is missing/null (Claude-CLI invocations).
    """
    script = task.get("script")
    if isinstance(script, str) and script.strip():
        first_token = script.strip().split()[0]
        # `python -m foo` and similar — first token is the interpreter, not a
        # logical job name. Bail to the skill-name fallback below.
        if first_token not in {"python", "python3", "uv", "uvx"}:
            stem = Path(first_token).stem
            return f"cron_{stem}.log"
    skill = task.get("skill", task["id"])
    return f"cron_{str(skill).replace('-', '_')}.log"


def _split_script(script: str) -> list[str]:
    """Shell-split a schedule ``script`` string into argv tokens.

    The schedule stores invocations as a single string that may embed CLI
    flags (``scripts/content_pipeline.py --stage publish``) or a module
    invocation (``python -m ideator.main``). launchd's ProgramArguments wants
    each token as its own element, so split with ``shlex`` semantics and drop a
    leading ``python``/``python3`` token (the plist already supplies the
    absolute venv interpreter as argv[0]).
    """
    tokens = shlex.split(script.strip())
    if tokens and tokens[0] in {"python", "python3"}:
        tokens = tokens[1:]
    return tokens


def _program_arguments(task: Flow, paths: PlistPaths) -> list[str]:
    """Build the ProgramArguments list for a task.

    Three flavours, matching the installed plists:
    1. Script + watchdog wrapper: `[python, watchdog, script.py, --timeout, 300]`
       Used for simple Playwright/REST scripts with no CLI args.
    2. Script with embedded args: `[python, "script.py", "--stage", "ideate"]`
       Each shell token is its own ProgramArguments element so launchd doesn't
       try to open a path that includes the flags. `python -m mod` -> `-m mod`.
    3. Claude-CLI skill: `[claude, --dangerously-skip-permissions, /<skill>]`
       Used when task.script is missing or null (skill runs via Claude Code).

    An optional ``args`` list on the flow is appended in every flavour, so a
    per-platform loop can pass ``["--platform", "instagram"]`` (forwarded to the
    child by the watchdog) or a skill positional like ``["instagram"]`` while
    keeping the watchdog wrapper intact.
    """
    extra_args = [str(a) for a in task.get("args", []) if str(a).strip()]
    script = task.get("script")
    if not isinstance(script, str) or not script.strip():
        skill = task.get("skill") or _task_short_name(task["id"], "")
        return [
            paths["claude_cli"],
            "--dangerously-skip-permissions",
            f"/{skill}",
            *extra_args,
        ]
    tokens = _split_script(script)
    if len(tokens) > 1:
        # Embedded args (or `-m module`): emit each token separately.
        return [paths["python"], *tokens, *extra_args]
    return [
        paths["python"],
        paths["watchdog_script"],
        *tokens,
        *extra_args,
        "--timeout",
        paths["watchdog_timeout"],
    ]


def compose_plist_xml(
    task: Flow,
    brand: dict[str, Any],
    paths: PlistPaths,
) -> bytes:
    """Render a single launchd plist as XML bytes.

    `task` is one entry from the brand schedule (id already has the brand prefix).
    `brand` is the full brand dict (must contain `brand.name` and `brand.key`).
    `paths` carries runtime conventions so tests can inject fixtures.
    """
    brand_meta = brand.get("brand", brand)
    brand_name_lower = str(brand_meta.get("name", "")).lower()
    brand_key = str(brand_meta.get("key", ""))
    if not brand_name_lower or not brand_key:
        raise ValueError("brand.name and brand.key required to compose plists")

    short = _task_short_name(task["id"], brand_key)
    cron = task.get("schedule", {}).get("cron")
    if not cron:
        raise ValueError(f"task {task['id']} has no schedule.cron")
    interval = cron_to_launchd(cron)

    log_path = f"{paths.get('log_dir', paths['working_dir'])}/{_log_basename(task)}"
    env_vars: dict[str, str] = {
        "PATH": paths["launchd_path"],
        "PYTHONUNBUFFERED": "1",
        "HOME": paths["home"],
        "BRAND_DIR": paths.get("brand_dir", ""),
    }

    plist: PlistDict = {
        "Label": f"com.{brand_name_lower}.{short}",
        "ProgramArguments": _program_arguments(task, paths),
        "WorkingDirectory": paths["working_dir"],
        "StartCalendarInterval": interval,
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
        "EnvironmentVariables": env_vars,
        "RunAtLoad": False,
    }
    return plistlib.dumps(plist)


def compose_brand_plists(
    brand_schedule_tasks: list[Flow],
    brand: dict[str, Any],
    paths: PlistPaths | None = None,
) -> dict[str, bytes]:
    """Render every brand task to its plist. Returns {filename: bytes}.

    Filename pattern: `com.<brand-name-lower>.<task-short>.plist`.
    Skips tasks that have no `schedule.cron` (e.g. on-demand utilities).
    Takes the already-composed brand schedule tasks rather than re-running
    `compose_brand_schedule_artifact` to keep this module decoupled.
    """
    if paths is None:
        paths = default_plist_paths()
    brand_meta = brand.get("brand", brand)
    brand_name_lower = str(brand_meta.get("name", "")).lower()
    brand_key = str(brand_meta.get("key", ""))
    out: dict[str, bytes] = {}
    for task in brand_schedule_tasks:
        if not task.get("schedule", {}).get("cron"):
            continue
        short = _task_short_name(task["id"], brand_key)
        filename = f"com.{brand_name_lower}.{short}.plist"
        out[filename] = compose_plist_xml(task, brand, paths)
    return out


def write_plist_dir(plists: dict[str, bytes], plist_dir: Path) -> list[Path]:
    """Atomic-replace each plist file in `plist_dir`. Returns written paths."""
    plist_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, payload in plists.items():
        target = plist_dir / filename
        tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}")
        tmp.write_bytes(payload)
        os.replace(tmp, target)
        written.append(target)
    return written


def check_plist_dir(plists: dict[str, bytes], plist_dir: Path) -> list[str]:
    """Return a list of human-readable drift reasons, empty if all in sync."""
    drift: list[str] = []
    if not plist_dir.exists():
        return [f"{plist_dir} does not exist"]
    on_disk = {p.name for p in plist_dir.glob("*.plist")}
    expected = set(plists.keys())
    for missing in sorted(expected - on_disk):
        drift.append(f"missing plist: {missing}")
    for stale in sorted(on_disk - expected):
        drift.append(f"stale plist: {stale}")
    for filename, payload in plists.items():
        target = plist_dir / filename
        if target.exists() and target.read_bytes() != payload:
            drift.append(f"out-of-date plist: {filename}")
    return drift


def install_plists(
    plist_dir: Path,
    apply: bool,
    launch_agents_dir: Path = LAUNCH_AGENTS_DIR,
    runner: Callable[..., Any] = subprocess.run,
) -> int:
    """Copy plists to ~/Library/LaunchAgents/ and `launchctl bootstrap` each.

    Dry-run by default — pass `apply=True` to actually mutate the filesystem
    and load the agents. `runner` is injected so tests can assert no subprocess
    calls happen under dry-run.
    """
    if not plist_dir.exists():
        sys.stderr.write(f"install: {plist_dir} does not exist; run build first.\n")
        return 1
    plists = sorted(plist_dir.glob("*.plist"))
    if not plists:
        sys.stderr.write(f"install: no plists found in {plist_dir}.\n")
        return 1
    uid = os.getuid()
    prefix = "[dry-run] " if not apply else ""
    for plist_path in plists:
        target = launch_agents_dir / plist_path.name
        sys.stdout.write(f"{prefix}cp {plist_path} -> {target}\n")
        bootout_cmd = ["launchctl", "bootout", f"gui/{uid}", str(target)]
        bootstrap_cmd = ["launchctl", "bootstrap", f"gui/{uid}", str(target)]
        sys.stdout.write(f"{prefix}{subprocess.list2cmdline(bootout_cmd)}\n")
        sys.stdout.write(f"{prefix}{subprocess.list2cmdline(bootstrap_cmd)}\n")
        if not apply:
            continue
        launch_agents_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(plist_path, target)
        # Best-effort bootout (may fail if not loaded) — don't bail.
        runner(bootout_cmd, capture_output=True, text=True, check=False)
        result = runner(bootstrap_cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            sys.stderr.write(
                f"install: bootstrap {target} failed (rc={result.returncode}): "
                f"{result.stderr.strip()}\n"
            )
    return 0


def uninstall_plists(
    brand: dict[str, Any],
    apply: bool,
    launch_agents_dir: Path = LAUNCH_AGENTS_DIR,
    runner: Callable[..., Any] = subprocess.run,
) -> int:
    """`launchctl bootout` + `rm` every `com.<brand>.*.plist` under LaunchAgents."""
    brand_meta = brand.get("brand", brand)
    brand_name_lower = str(brand_meta.get("name", "")).lower()
    if not brand_name_lower:
        sys.stderr.write("uninstall: brand.name required to scope removal.\n")
        return 1
    pattern = f"com.{brand_name_lower}.*.plist"
    targets = sorted(launch_agents_dir.glob(pattern))
    if not targets:
        sys.stdout.write(f"uninstall: no plists matching {pattern} in {launch_agents_dir}.\n")
        return 0
    uid = os.getuid()
    prefix = "[dry-run] " if not apply else ""
    for target in targets:
        bootout_cmd = ["launchctl", "bootout", f"gui/{uid}", str(target)]
        sys.stdout.write(f"{prefix}{subprocess.list2cmdline(bootout_cmd)}\n")
        sys.stdout.write(f"{prefix}rm {target}\n")
        if not apply:
            continue
        runner(bootout_cmd, capture_output=True, text=True, check=False)
        try:
            target.unlink()
        except OSError as exc:
            sys.stderr.write(f"uninstall: rm {target} failed: {exc}\n")
    return 0
