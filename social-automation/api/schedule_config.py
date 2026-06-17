# pyright: reportMissingImports=false
"""schedule.json loader + dep sort + input freshness checks.

Reads ``settings.paths.schedule_file``, validates per-task shape with
pydantic, exposes:

- ``load_schedule_config`` -- parse + validate; returns ``ScheduleConfig``.
- ``label_for_task_id`` -- maps ``dogfood-<suffix>`` -> ``com.dogfoodandfun.<suffix>``.
- ``task_for_label`` -- reverse lookup; returns ``ScheduleTask | None``.
- ``check_inputs_satisfied`` -- for each declared input, verify existence,
  optional ``min_count`` (non-empty list / dict items), and optional
  ``max_age_hours`` against mtime. Returns ``(ok: bool, [InputStatus])``.
- ``topological_order`` -- sort tasks by ``order`` (ascending, None last),
  then label. Detects dep cycles and raises ``DependencyCycleError``.

"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.schedule_schemas import InputStatus


def read_json(path: Path | str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
from lib.config import BrandPaths
from lib.config import settings as _settings

_log = logging.getLogger("approval_api.schedule_config")

_BRAND_LABEL_PREFIX = "com.dogfoodandfun."
_TASK_ID_PREFIX = "dogfood-"


class ScheduleInput(BaseModel):
    """One declared input artifact for a scheduled task."""

    model_config = ConfigDict(extra="allow")

    path: str
    min_count: int = 0
    max_age_hours: float | None = None
    produced_by: str | None = None


class ScheduleTask(BaseModel):
    """A task entry from ``schedule.json``.

    Mirrors only the fields the API surface needs; extra keys are kept
    via ``extra="allow"`` so unrelated metadata round-trips cleanly.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    skill: str | None = None
    order: int | None = None
    output_file: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    inputs: list[ScheduleInput] = Field(default_factory=list)


class ScheduleConfig(BaseModel):
    """Top-level schedule.json shape."""

    tasks: list[ScheduleTask] = Field(default_factory=list)


class DependencyCycleError(Exception):
    """Raised by ``topological_order`` when ``depends_on`` graph has a cycle."""


def _paths() -> BrandPaths:
    return cast(BrandPaths, _settings.paths)


def label_for_task_id(task_id: str) -> str | None:
    """Map ``dogfood-<suffix>`` -> ``com.dogfoodandfun.<suffix>``.

    Returns None when ``task_id`` doesn't carry the brand prefix.
    """
    if not task_id.startswith(_TASK_ID_PREFIX):
        return None
    suffix = task_id[len(_TASK_ID_PREFIX):]
    if not suffix:
        return None
    return f"{_BRAND_LABEL_PREFIX}{suffix}"


def load_schedule_config() -> ScheduleConfig:
    """Parse + validate ``schedule.json``.

    On any failure (missing file, parse error, validation error) returns
    an empty ``ScheduleConfig`` and logs a warning.
    """
    try:
        schedule_file = _paths().schedule_file
    except Exception as exc:  # settings may be unbound in tests
        _log.warning("schedule_file path unresolved: %s", exc)
        return ScheduleConfig(tasks=[])

    if not schedule_file.exists():
        _log.warning("schedule.json not found at %s", schedule_file)
        return ScheduleConfig(tasks=[])

    data = read_json(schedule_file)
    if data is None:
        _log.warning("schedule.json unreadable / malformed: %s", schedule_file)
        return ScheduleConfig(tasks=[])

    if isinstance(data, dict):
        raw_tasks = data.get("tasks", [])
    elif isinstance(data, list):
        raw_tasks = data
    else:
        _log.warning("schedule.json root is %s, expected dict|list", type(data).__name__)
        return ScheduleConfig(tasks=[])

    if not isinstance(raw_tasks, list):
        _log.warning("schedule.json 'tasks' is %s, expected list", type(raw_tasks).__name__)
        return ScheduleConfig(tasks=[])

    try:
        return ScheduleConfig(tasks=raw_tasks)
    except ValidationError as exc:
        _log.warning("schedule.json failed pydantic validation: %s", exc)
        return ScheduleConfig(tasks=[])


def task_for_label(
    label: str,
    config: ScheduleConfig | None = None,
) -> ScheduleTask | None:
    """Reverse-lookup a ``ScheduleTask`` by its launchd label."""
    if not label.startswith(_BRAND_LABEL_PREFIX):
        return None
    suffix = label[len(_BRAND_LABEL_PREFIX):]
    if not suffix:
        return None
    task_id = f"{_TASK_ID_PREFIX}{suffix}"

    cfg = config if config is not None else load_schedule_config()
    for task in cfg.tasks:
        if task.id == task_id:
            return task
    return None


def _count_artifact(data: object) -> int:
    """Count rows in a parsed artifact (list/dict -> len, scalar -> 1)."""
    if isinstance(data, (list, dict)):
        return len(data)
    if data is None:
        return 0
    return 1


def _format_age(age_hours: float, max_age_hours: float) -> str:
    return f"stale (age {age_hours:.1f}h > {max_age_hours:g}h)"


def check_inputs_satisfied(
    task: ScheduleTask,
    brand_dir: Path | None = None,
) -> tuple[bool, list[InputStatus]]:
    """Check every declared input for existence, count, freshness.

    Returns ``(all_ok, statuses)``. A task with no inputs is trivially OK.
    """
    base: Path = brand_dir if brand_dir is not None else _paths().brand_dir
    now = datetime.now(tz=UTC)

    statuses: list[InputStatus] = []
    all_ok = True

    for spec in task.inputs:
        path = (base / spec.path).resolve() if not Path(spec.path).is_absolute() else Path(spec.path)
        exists = path.exists()
        count = 0
        age_hours: float | None = None
        reason: str | None = None
        ok = True

        if not exists:
            ok = False
            reason = "file missing"
        else:
            data = read_json(path)
            count = _count_artifact(data) if data is not None else 1
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            age_hours = (now - mtime).total_seconds() / 3600.0

            if count < spec.min_count:
                ok = False
                reason = f"empty (got {count}, need {spec.min_count})"
            elif spec.max_age_hours is not None and age_hours > spec.max_age_hours:
                ok = False
                reason = _format_age(age_hours, spec.max_age_hours)

        statuses.append(InputStatus(
            path=spec.path,
            exists=exists,
            count=count,
            age_hours=age_hours,
            ok=ok,
            reason=reason,
        ))
        if not ok:
            all_ok = False

    return all_ok, statuses


def topological_order(tasks: list[ScheduleTask]) -> list[ScheduleTask]:
    """Kahn's algorithm by ``depends_on``.

    Within a topological level, sort by ``(order is None, order or 0, id)``
    so explicitly-ordered tasks fire first and unordered tasks fall to
    the end alphabetically. Raises ``DependencyCycleError`` on cycle.
    """
    by_id: dict[str, ScheduleTask] = {t.id: t for t in tasks}
    indeg: dict[str, int] = {t.id: 0 for t in tasks}
    # Edges: dep -> dependent.
    edges: dict[str, list[str]] = {t.id: [] for t in tasks}

    for task in tasks:
        for dep in task.depends_on:
            if dep not in by_id:
                # Unknown dep — skip silently; not our job to fail the whole list.
                continue
            edges[dep].append(task.id)
            indeg[task.id] += 1

    def _level_key(task_id: str) -> tuple[bool, int, str]:
        task = by_id[task_id]
        order = task.order
        return (order is None, order if order is not None else 0, task.id)

    ready = sorted([tid for tid, d in indeg.items() if d == 0], key=_level_key)
    out: list[ScheduleTask] = []

    while ready:
        tid = ready.pop(0)
        out.append(by_id[tid])
        new_ready: list[str] = []
        for nxt in edges[tid]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                new_ready.append(nxt)
        if new_ready:
            ready = sorted(ready + new_ready, key=_level_key)

    if len(out) != len(tasks):
        raise DependencyCycleError(
            f"cycle detected in schedule.json depends_on graph "
            f"({len(out)}/{len(tasks)} tasks ordered)"
        )
    return out


def annotate_schedule_entries(entries: list[dict[str, Any]]) -> None:
    """Annotate launchctl entries (mutated in-place) with schedule.json
    ``order`` / ``depends_on`` plus the input-freshness check result.

    Entries without a matching task in ``schedule.json`` are left
    untouched; ``ScheduleEntry`` defaults take care of the missing keys.
    """
    config = load_schedule_config()
    for entry in entries:
        task = task_for_label(entry["label"], config)
        if task is None:
            continue
        entry["order"] = task.order
        entry["output_file"] = getattr(task, "output_file", None)
        entry["depends_on"] = list(task.depends_on)
        ok, statuses = check_inputs_satisfied(task)
        entry["inputs_satisfied"] = ok
        entry["input_status"] = [s.model_dump() for s in statuses]


__all__ = [
    "DependencyCycleError",
    "ScheduleConfig",
    "ScheduleInput",
    "ScheduleTask",
    "annotate_schedule_entries",
    "check_inputs_satisfied",
    "label_for_task_id",
    "load_schedule_config",
    "task_for_label",
    "topological_order",
]
