"""Re-run guard: skip a runner if it ran successfully within the window.

Replaces 6 inline copies of the `last_run.json` check pattern across
`scripts/*.py` (comment_poster, wp_scan, ig_scan, fb_group_scout,
daily_wp_draft, fb_scan). Single source of truth for the read+write
of `.claude/state/last_run.json`.

Usage as a context manager:

    from lib.scheduling import once_per, record_run

    with once_per("comment-poster", hours=24, force=("--force" in sys.argv)):
        # ... runner work ...
        record_run("comment-poster", status="success", extra={"posted": 5})

If the previous run was within the window AND `force` is False, the
context manager raises `AlreadyRanError` immediately (no body executes).
Callers catch it at the runner's outermost `try` and exit cleanly.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict

from lib.errors.base import SocialAutomationError
from lib.io.jsonio import read_json, write_json

_DEFAULT_LAST_RUN_FILE = (
    Path(__file__).resolve().parent.parent.parent / ".claude/state/last_run.json"
)


class _LastRunRecord(TypedDict, total=False):
    last_run_at: str  # ISO-8601 with Z suffix or +00:00
    status: str  # "success", "failed", "skipped"
    detail: str  # human-readable note


class AlreadyRanError(SocialAutomationError):
    """A successful run within the window already exists; skip this invocation.

    Not a `RetryableError` and not a `PermanentError` — it's a clean
    skip signal. Runners catch it at the outermost handler and exit 0.
    """


def last_run_status(
    skill: str,
    *,
    last_run_file: Path | None = None,
) -> _LastRunRecord | None:
    """Read the most recent run record for `skill`, or None if never run.

    Args:
        skill: Skill name (the key in last_run.json).
        last_run_file: Override path (mostly for tests).

    Returns:
        The full record (`{last_run_at, status, ...}`) or None.
    """
    path = last_run_file or _DEFAULT_LAST_RUN_FILE
    all_runs: dict[str, _LastRunRecord] = read_json(path, default={})  # type: ignore[assignment]
    return all_runs.get(skill)


def record_run(
    skill: str,
    *,
    status: str = "success",
    extra: dict[str, object] | None = None,
    last_run_file: Path | None = None,
    when: datetime | None = None,
) -> None:
    """Record the completion of a run.

    Always call at the END of a runner, regardless of outcome. Pass
    `status="failed"` on errors so the next invocation won't be skipped
    by `once_per` (which only skips on prior `status=="success"`).

    Args:
        skill: Skill name.
        status: "success" | "failed" | "skipped" (or any other tag).
        extra: Optional metadata merged into the record (e.g. counts).
            Don't put secrets here — this file is committed-adjacent.
        last_run_file: Override path (tests).
        when: Override timestamp (tests). UTC.
    """
    path = last_run_file or _DEFAULT_LAST_RUN_FILE
    now = when or datetime.now(UTC)
    all_runs: dict[str, _LastRunRecord] = read_json(path, default={})  # type: ignore[assignment]
    record: _LastRunRecord = {
        "last_run_at": now.isoformat().replace("+00:00", "Z"),
        "status": status,
    }
    if extra:
        # TypedDict with total=False is permissive — extra keys allowed
        # at runtime, mypy errors silenced via cast.
        for k, v in extra.items():
            record[k] = v  # type: ignore[literal-required]
    all_runs[skill] = record
    write_json(path, all_runs)


@contextmanager
def once_per(
    skill: str,
    *,
    hours: int = 24,
    force: bool = False,
    last_run_file: Path | None = None,
    now: datetime | None = None,
) -> Iterator[None]:
    """Context manager: skip the body if `skill` ran successfully within `hours`.

    Args:
        skill: Skill name (key in last_run.json).
        hours: Window length. Default 24h matches the most common cron cadence.
        force: Bypass the guard. Pass `--force in sys.argv` from the runner.
        last_run_file: Override path (tests).
        now: Override "current" time (tests). UTC.

    Raises:
        AlreadyRanError: If a successful run is within the window AND
            `force` is False. The body of the `with` block is NOT
            executed. Catch at the runner's outermost handler.

    Behavior matrix:
        no prior run                    → run (body executes)
        prior status=="failed"          → run (failed runs don't gate)
        prior status=="success", outside → run
        prior status=="success", inside  → skip (raise AlreadyRanError)
        prior status=="success", inside, force=True → run
    """
    if force:
        yield
        return

    record = last_run_status(skill, last_run_file=last_run_file)
    if record is not None and record.get("status") == "success":
        last_run_at = record.get("last_run_at", "")
        if last_run_at and _within_window(last_run_at, hours, now):
            raise AlreadyRanError(
                f"{skill} already ran successfully within {hours}h",
                context={"skill": skill, "last_run_at": last_run_at, "window_hours": hours},
            )
    yield


def _within_window(iso_timestamp: str, hours: int, now: datetime | None) -> bool:
    """True if `iso_timestamp` is within `hours` of `now` (UTC)."""
    try:
        # Normalize Z → +00:00 for fromisoformat (Python 3.11+ tolerates Z, but be safe).
        normalized = iso_timestamp.replace("Z", "+00:00")
        last = datetime.fromisoformat(normalized)
    except ValueError:
        # Malformed timestamp → treat as "no valid prior run" so we don't
        # silently block the runner forever.
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return (current - last) < timedelta(hours=hours)
