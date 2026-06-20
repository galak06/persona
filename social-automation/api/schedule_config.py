# pyright: reportMissingImports=false
"""Schedule config loader: schedule.db is the sole source of truth.

Exposes load_schedule_config, label_for_task_id, task_for_label,
check_inputs_satisfied, topological_order, annotate_schedule_entries.
"""

from __future__ import annotations

import json
import logging
import os
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
    """A task entry from ``schedule.db``.

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
    """Top-level schedule config shape."""

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


def _load_from_db(db_path: Path) -> ScheduleConfig | None:
    """Load tasks from schedule.db; returns None on any error."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from lib import schedule_db  # noqa: PLC0415
        conn = schedule_db.connect(str(db_path))
        try:
            rows = schedule_db.load_all(conn)
        finally:
            conn.close()
        raw_tasks: list[dict[str, Any]] = []
        for row in rows:
            task: dict[str, Any] = dict(row)
            task["order"] = task.pop("order_num", None)  # DB uses order_num
            extra = task.pop("extra", None) or {}
            task.update(extra)
            raw_tasks.append(task)
        config = ScheduleConfig(tasks=raw_tasks)
        _log.info("schedule config: DB %s (%d tasks)", db_path, len(config.tasks))
        return config
    except ValidationError as exc:
        _log.warning("schedule.db validation failed: %s", exc)
        return None
    except Exception as exc:
        _log.warning("schedule.db load error: %s", exc)
        return None


def load_schedule_config() -> ScheduleConfig:
    """Parse + validate schedule config from ``$BRAND_DIR/data/db/schedule.db``.

    Raises ``RuntimeError`` if ``BRAND_DIR`` is unset, the DB file is missing,
    or the DB cannot be loaded.
    """
    brand_dir = os.environ.get("BRAND_DIR")
    if not brand_dir:
        raise RuntimeError("BRAND_DIR environment variable is not set; cannot locate schedule.db")

    db_path = Path(brand_dir) / "data" / "db" / "schedule.db"
    if not db_path.exists():
        raise RuntimeError(f"schedule.db not found at {db_path}")

    result = _load_from_db(db_path)
    if result is None:
        raise RuntimeError(f"Failed to load schedule config from {db_path}; check logs for details")

    return result


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
    """Check every declared input for existence, count, and freshness."""
    base: Path = brand_dir if brand_dir is not None else _paths().brand_dir
    now = datetime.now(tz=UTC)
    statuses: list[InputStatus] = []
    all_ok = True
    for spec in task.inputs:
        p = (base / spec.path).resolve() if not Path(spec.path).is_absolute() else Path(spec.path)
        exists = p.exists()
        count, age_hours, reason, ok = 0, None, None, True
        if not exists:
            ok, reason = False, "file missing"
        else:
            data = read_json(p)
            count = _count_artifact(data) if data is not None else 1
            age_hours = (now - datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)).total_seconds() / 3600.0
            if count < spec.min_count:
                ok, reason = False, f"empty (got {count}, need {spec.min_count})"
            elif spec.max_age_hours is not None and age_hours > spec.max_age_hours:
                ok, reason = False, _format_age(age_hours, spec.max_age_hours)
        statuses.append(InputStatus(path=spec.path, exists=exists, count=count, age_hours=age_hours, ok=ok, reason=reason))
        if not ok:
            all_ok = False
    return all_ok, statuses


def topological_order(tasks: list[ScheduleTask]) -> list[ScheduleTask]:
    """Kahn's algorithm; level-sort by (order is None, order, id).
    Raises ``DependencyCycleError`` on cycle."""
    by_id: dict[str, ScheduleTask] = {t.id: t for t in tasks}
    indeg: dict[str, int] = {t.id: 0 for t in tasks}
    edges: dict[str, list[str]] = {t.id: [] for t in tasks}  # dep -> dependents

    for task in tasks:
        for dep in task.depends_on:
            if dep not in by_id:
                continue  # unknown dep — skip silently
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
            f"cycle detected in schedule depends_on graph "
            f"({len(out)}/{len(tasks)} tasks ordered)"
        )
    return out


def annotate_schedule_entries(entries: list[dict[str, Any]]) -> None:
    """Annotate launchctl entries (mutated in-place) with order/depends_on
    and the input-freshness check. Entries without a matching task are left
    untouched.
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
