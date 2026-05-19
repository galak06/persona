# pyright: reportMissingImports=false
"""Pydantic models for the schedule + trigger surface.

Split out of ``api.schemas`` to keep that module under the 300-line cap.
Re-exported from ``api.schemas`` so existing import paths keep working.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class InputStatus(BaseModel):
    """Per-input freshness check result for a scheduled task.

    Emitted by ``check_inputs_satisfied`` in ``api.schedule_config``. The
    ``ok`` flag is the AND of the existence, count, and age checks. A
    failed check writes a human-readable ``reason`` so the UI can render
    it as a tooltip without re-running the check client-side.
    """

    path: str
    exists: bool
    count: int
    age_hours: float | None
    ok: bool
    reason: str | None = None


class ScheduleEntry(BaseModel):
    """One launchd job in the ``/flows/state`` schedule list.

    Sourced from ``launchctl list`` + ``~/Library/LaunchAgents/com.<brand>.*``
    plist files. ``is_loaded`` is True iff the label currently appears in
    ``launchctl list`` output. ``last_exit_code`` is the second column from
    launchctl (parsed int, or None when not loaded / waiting). ``flow_id``
    maps the cron label back to the parent flow when one exists, else None.
    """

    label: str
    flow_id: str | None = None
    schedule_human: str
    last_fire_at: datetime | None = None
    last_exit_code: int | None = None
    script_path: str | None = None
    log_path: str | None = None
    is_loaded: bool
    order: int | None = None
    output_file: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    inputs_satisfied: bool = True
    input_status: list[InputStatus] = Field(default_factory=list)


class LogTailResponse(BaseModel):
    """Envelope for ``GET /api/v1/schedule/{label}/log``."""

    label: str
    path: str | None
    lines: list[str]
    truncated: bool


class MissingFlowEntry(BaseModel):
    """One scheduled flow that's defined in schedule.json but not loaded in launchctl."""

    label: str
    plist_path: str | None = None
    command: str  # the launchctl bootstrap line to fix it


class MissingFlowsResponse(BaseModel):
    """Envelope for ``GET /api/v1/schedule/missing``."""

    missing: list[MissingFlowEntry]
    as_of: str


class TriggerResponse(BaseModel):
    """Envelope for ``POST /api/v1/schedule/{label}/trigger``."""

    ok: bool
    message: str
    label: str


__all__ = [
    "InputStatus",
    "LogTailResponse",
    "MissingFlowEntry",
    "MissingFlowsResponse",
    "ScheduleEntry",
    "TriggerResponse",
]
