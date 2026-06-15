# pyright: reportMissingImports=false
"""launchd / plist scanner for ``/api/v1/flows/state`` schedule entries.

``collect_schedule_state`` shells out to ``launchctl list``, parses the
tab-separated output, cross-references the ``~/Library/LaunchAgents``
plist directory, and returns one ``ScheduleEntry`` dict per
``com.dogfoodandfun.*`` job.

No subprocess command ever uses ``shell=True``. All args are passed as
a list. ``launchctl list`` runs with a 10s timeout — if it hangs or
isn't installed (CI), we collapse to an empty load-set so the plist
walk still produces entries (just with ``is_loaded=False`` everywhere).
"""

from __future__ import annotations

import logging
import plistlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from api.schedule_config import annotate_schedule_entries
from lib.config import BrandPaths
from lib.config import settings as _settings

_log = logging.getLogger("approval_api.schedule_state")

_BRAND_LABEL_PREFIX = "com.dogfoodandfun."


def _paths() -> BrandPaths:
    return cast(BrandPaths, _settings.paths)


# Map plist short-name (label suffix) → parent flow_id. Anything not in the
# map maps to None and renders unparented in the UI.
_LABEL_TO_FLOW: dict[str, str] = {
    "fb-scanner": "engagement-comment",
    "fb-comment": "engagement-comment",
    "ig-scanner": "engagement-comment",
    "ig-comment": "engagement-comment",
    "comment-approver": "engagement-comment",
    "comment-poster": "engagement-comment",
    "content-pipeline": "blog-campaign",
    "daily-wp-draft": "blog-campaign",
    "auto-drafter": "blog-campaign",
    "content-ideator": "blog-campaign",
    "content-publish": "blog-campaign",
    "recipe-ideator": "blog-campaign",
    "recipe-publisher": "blog-campaign",
    "fb-group-scout": "community-growth",
    "fb-group-distribute": "community-growth",
    "fb-notification-scan": "community-growth",
    "reply-follower": "social-loyalty",
    "reply-follower-morning": "social-loyalty",
    "reply-follower-evening": "social-loyalty",
    "ig-own-comments": "social-loyalty",
    "refresh-trends": "market-intel",
    "refresh-keyword-research": "market-intel",
    "campaign-worker": "brand-campaigns",
}

# Map plist short-name → cron log filename (in settings.paths.logs_dir).
_LABEL_TO_LOG: dict[str, str] = {
    "fb-scanner": "cron_fb_scan.log",
    "fb-comment": "cron_fb_comment.log",
    "ig-scanner": "cron_ig_scan.log",
    "ig-comment": "cron_ig_comment.log",
    "comment-approver": "cron_comment_approver.log",
    "comment-poster": "cron_comment_poster.log",
    "content-pipeline": "cron_content_pipeline.log",
    "daily-wp-draft": "cron_content_pipeline.log",
    "auto-drafter": "cron_auto_drafter.log",
    "content-ideator": "cron_content_pipeline.log",
    "content-publish": "cron_content_pipeline.log",
    "recipe-ideator": "cron_recipe_ideator.log",
    "recipe-publisher": "cron_recipe_publisher.log",
    "fb-group-scout": "cron_fb_scout.log",
    "fb-group-distribute": "cron_fb_group_distribute.log",
    "fb-notification-scan": "cron_fb_notification_scan.log",
    "reply-follower": "cron_reply_follower.log",
    "reply-follower-morning": "cron_reply_follower.log",
    "reply-follower-evening": "cron_reply_follower.log",
    "ig-own-comments": "cron_ig_own_comments.log",
    "refresh-trends": "cron_refresh_trends.log",
    "refresh-keyword-research": "cron_refresh_trends.log",
    "campaign-worker": "cron_campaign_worker.log",
}

_WEEKDAYS = {
    0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed",
    4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun",
}


_LAUNCHCTL_BIN = "/bin/launchctl"


def _parse_launchctl_int(cell: str) -> int | None:
    """Convert launchctl list cell to int — '-' / empty / non-int → None."""
    if not cell or cell == "-":
        return None
    try:
        return int(cell)
    except ValueError:
        return None


def _run_launchctl_list() -> dict[str, tuple[int | None, int | None]]:
    """Return ``{label: (pid, exit_code)}`` for every ``com.dogfoodandfun.*``
    label currently loaded into launchd."""
    try:
        result = subprocess.run(
            [_LAUNCHCTL_BIN, "list"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        _log.warning("launchctl list failed: %s", exc)
        return {}
    if result.returncode != 0:
        _log.warning(
            "launchctl list returncode=%d stderr=%s",
            result.returncode, (result.stderr or "")[:200],
        )
        return {}

    loaded: dict[str, tuple[int | None, int | None]] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        pid_token, exit_token, label = parts
        if not label.startswith(_BRAND_LABEL_PREFIX):
            continue
        loaded[label] = (
            _parse_launchctl_int(pid_token),
            _parse_launchctl_int(exit_token),
        )
    return loaded


def _format_calendar_interval(value: Any) -> str:
    """Render ``StartCalendarInterval`` dict / list-of-dicts to a human
    string. Falls back to the JSON repr if the shape is unrecognised."""
    if isinstance(value, dict):
        weekday = value.get("Weekday")
        hour = value.get("Hour")
        minute = value.get("Minute")
        time_str = ""
        if hour is not None and minute is not None:
            time_str = f"{int(hour):02d}:{int(minute):02d}"
        elif hour is not None:
            time_str = f"{int(hour):02d}:00"
        elif minute is not None:
            time_str = f":{int(minute):02d}"
        if weekday is not None:
            return f"weekly {_WEEKDAYS.get(int(weekday), weekday)} {time_str}".strip()
        if time_str:
            return f"daily {time_str}"
        return repr(value)
    if isinstance(value, list):
        # Common shape: hourly fan-out — same Minute, sweep of Hours.
        minutes_set = {
            item.get("Minute") for item in value if isinstance(item, dict)
        }
        hours_raw = [
            item.get("Hour") for item in value if isinstance(item, dict)
        ]
        hours_int: list[int] = [int(h) for h in hours_raw if h is not None]
        if (
            len(minutes_set) == 1
            and len(hours_int) == len(hours_raw)
            and len(hours_int) > 1
        ):
            minute = next(iter(minutes_set))
            min_h, max_h = min(hours_int), max(hours_int)
            mm = f":{int(minute):02d}" if minute is not None else ""
            return f"hourly {mm} {min_h:02d}-{max_h:02d}"
        # Otherwise just list each as "daily HH:MM"
        slots = [_format_calendar_interval(item) for item in value]
        return ", ".join(slots)
    return repr(value)


def _format_schedule(plist_data: dict[str, Any]) -> str:
    interval = plist_data.get("StartInterval")
    if isinstance(interval, int):
        return f"every {interval}s"
    calendar = plist_data.get("StartCalendarInterval")
    if calendar is not None:
        return _format_calendar_interval(calendar)
    if plist_data.get("RunAtLoad"):
        return "run-at-load"
    if plist_data.get("KeepAlive"):
        return "keep-alive"
    return "manual"


def _load_plist(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as fp:
            data = plistlib.load(fp)
        return data if isinstance(data, dict) else None
    except (OSError, plistlib.InvalidFileException, ValueError) as exc:
        _log.warning("plist load failed for %s: %s", path, exc)
        return None


def _last_fire_at(label_suffix: str) -> datetime | None:
    log_name = _LABEL_TO_LOG.get(label_suffix)
    if not log_name:
        return None
    log_path = _paths().logs_dir / log_name
    if not log_path.exists():
        return None
    return datetime.fromtimestamp(log_path.stat().st_mtime, tz=UTC)


def collect_schedule_state() -> list[dict[str, Any]]:
    """Return one dict per ``com.dogfoodandfun.*`` launchd job.

    Sources: ``launchctl list`` plus ``~/Library/LaunchAgents`` (plists
    for jobs not currently loaded). Sorted by schedule.json ``order``.
    """
    loaded = _run_launchctl_list()
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plists: list[Path] = sorted(plist_dir.glob(f"{_BRAND_LABEL_PREFIX}*.plist"))

    entries: list[dict[str, Any]] = []
    seen_labels: set[str] = set()

    for plist_path in plists:
        label = plist_path.stem
        seen_labels.add(label)
        suffix = label.removeprefix(_BRAND_LABEL_PREFIX)

        data = _load_plist(plist_path) or {}
        schedule_human = _format_schedule(data)

        # Extract the script path from ProgramArguments. Most plists wrap the
        # real script in ``scripts/run_with_watchdog.py <script> --timeout N``,
        # so prefer the last ``*.py`` arg that isn't the watchdog itself; fall
        # back to the trailing arg if no ``.py`` shows up.
        program_args = data.get("ProgramArguments")
        script_path: str | None = None
        if isinstance(program_args, list) and program_args:
            py_args = [
                a for a in program_args
                if isinstance(a, str)
                and a.endswith(".py")
                and not a.endswith("run_with_watchdog.py")
            ]
            if py_args:
                script_path = py_args[-1]
            else:
                last_arg = program_args[-1]
                if isinstance(last_arg, str):
                    script_path = last_arg

        stdout_path = data.get("StandardOutPath")
        log_path: str | None = stdout_path if isinstance(stdout_path, str) else None

        pid_exit = loaded.get(label)
        is_loaded = pid_exit is not None
        last_exit_code = pid_exit[1] if pid_exit is not None else None

        entries.append({
            "label": label,
            "flow_id": _LABEL_TO_FLOW.get(suffix),
            "schedule_human": schedule_human,
            "last_fire_at": _last_fire_at(suffix),
            "last_exit_code": last_exit_code,
            "script_path": script_path,
            "log_path": log_path,
            "is_loaded": is_loaded,
        })

    # Labels loaded in launchctl but with no corresponding plist on disk.
    for label, (_pid, exit_code) in loaded.items():
        if label in seen_labels:
            continue
        suffix = label.removeprefix(_BRAND_LABEL_PREFIX)
        entries.append({
            "label": label,
            "flow_id": _LABEL_TO_FLOW.get(suffix),
            "schedule_human": "unknown (no plist)",
            "last_fire_at": _last_fire_at(suffix),
            "last_exit_code": exit_code,
            "script_path": None,
            "log_path": None,
            "is_loaded": True,
        })

    annotate_schedule_entries(entries)
    entries.sort(key=lambda e: (
        e.get("order") is None,
        e.get("order") if e.get("order") is not None else 9999,
        e["label"],
    ))
    return entries


__all__ = ["collect_schedule_state"]
