"""Per-campaign stage executor.

Extracted from ``scripts/campaign_worker.py`` (the inline
``process_campaign`` body). The outer cron loop, Telegram notifier, and
campaign-selection logic remain in the worker script. Per-task hook
execution lives in :mod:`lib.campaigns._executors`; lock acquisition in
:mod:`lib.campaigns._lock`.

Behaviour preserved verbatim:
    - ``worker.lock`` per campaign with 1-hour stale fallback.
    - Sequential tasks with ``current_task_index`` resume on failure.
    - ``state.json`` written atomically after each task increment.
    - On full ``publish`` success: ``ready/`` → ``published/<UTC-ts>/``.
    - ``state.history[]`` entries gain a ``"stage"`` field — additive,
      ignored by old readers.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from api.campaign_schemas import (
    CampaignConfig,
    CampaignState,
    CampaignTask,
    CustomHookTask,
)
from lib.campaigns._executors import execute as _execute_task
from lib.campaigns._lock import LockHeldError, acquire as _acquire_lock
from lib.io.jsonio import read_json, write_json
from lib.observability.logger import get_logger

__all__ = ["CampaignRunResult", "LockHeldError", "run_campaign"]

log = get_logger(__name__)

Stage = Literal["publish", "prepare"]


@dataclass(frozen=True)
class CampaignRunResult:
    """Outcome of a single ``run_campaign`` call.

    Attributes:
        ok: True iff every task in the stage completed.
        tasks_run: Tasks executed in THIS invocation (resumed runs report
            only the count run this call, not the stage total).
        error: Failure description or None.
        published_folder: Name of ``published/<ts>/`` directory — set only
            when ``stage="publish"`` and every task succeeded.
    """

    ok: bool
    tasks_run: int
    error: str | None
    published_folder: str | None


def run_campaign(
    campaign_dir: Path,
    *,
    stage: Stage = "publish",
    dry_run: bool = False,
) -> CampaignRunResult:
    """Execute one stage of a campaign.

    Reads ``campaign_config.json`` + ``state.json`` from ``campaign_dir``.
    Acquires ``worker.lock`` (raises :class:`LockHeldError` if held).
    Runs the selected stage's tasks sequentially with
    ``current_task_index`` resume logic. Atomically updates ``state.json``
    after each task. On full success of ``stage="publish"``, promotes
    ``ready/`` contents into ``published/<UTC-timestamp>/``.

    Args:
        campaign_dir: Absolute path to one campaign directory under
            ``<BRAND_DIR>/campaigns/``.
        stage: ``"publish"`` (default) runs ``publish_tasks`` — the
            :class:`CampaignConfig` validator auto-migrates the legacy
            ``tasks`` field into ``publish_tasks``. ``"prepare"`` runs
            ``prepare_tasks``.
        dry_run: Log the planned tasks but do not execute hooks, write
            ``state.json``, or promote ``ready/``.

    Returns:
        :class:`CampaignRunResult` with ``ok=True`` on full success or
        ``ok=False`` on task failure (``state.json`` already updated).

    Raises:
        LockHeldError: Another live process is running this campaign.
        FileNotFoundError: ``campaign_config.json`` missing.
    """
    config_file = campaign_dir / "campaign_config.json"
    state_file = campaign_dir / "state.json"
    lock_file = campaign_dir / "worker.lock"
    if not config_file.exists():
        raise FileNotFoundError(f"campaign_config.json missing in {campaign_dir}")

    config = CampaignConfig.model_validate(read_json(config_file, default={}))
    state_raw = read_json(state_file, default={})
    state = CampaignState.model_validate(state_raw if isinstance(state_raw, dict) else {})
    tasks: Sequence[CampaignTask] = (
        config.publish_tasks if stage == "publish" else config.prepare_tasks
    )
    if not tasks:
        log.info("campaign_stage_empty", campaign=campaign_dir.name, stage=stage)
        return CampaignRunResult(True, 0, None, None)

    if dry_run:
        return _dry_run_plan(campaign_dir, tasks, state, stage)

    with _acquire_lock(lock_file, campaign_dir.name):
        return _run_locked(campaign_dir, state, tasks, stage, state_file)


def _dry_run_plan(
    campaign_dir: Path,
    tasks: Sequence[CampaignTask],
    state: CampaignState,
    stage: Stage,
) -> CampaignRunResult:
    start = state.current_task_index
    for i in range(start, len(tasks)):
        task = tasks[i]
        summary = (
            f"{task.script_path}:{task.function}"
            if isinstance(task, CustomHookTask)
            else f"{task.platform}:{task.action}"
        )
        log.info(
            "campaign_dry_run_task",
            campaign=campaign_dir.name,
            stage=stage,
            index=i,
            total=len(tasks),
            task_type=task.type,
            task_summary=summary,
        )
    return CampaignRunResult(True, len(tasks) - start, None, None)


def _run_locked(
    campaign_dir: Path,
    state: CampaignState,
    tasks: Sequence[CampaignTask],
    stage: Stage,
    state_file: Path,
) -> CampaignRunResult:
    """Run the stage's tasks while holding the lock."""
    ready_dir = campaign_dir / "ready"
    published_dir = campaign_dir / "published"
    now = datetime.now(timezone.utc)
    # Snapshot ready/ BEFORE executing tasks — a task that drops new files
    # into ready/ must not get them swept into published/.
    items_to_promote: list[Path] = (
        list(ready_dir.iterdir()) if (stage == "publish" and ready_dir.exists()) else []
    )
    log.info(
        "campaign_run_started",
        campaign=campaign_dir.name,
        stage=stage,
        total_tasks=len(tasks),
        resume_from=state.current_task_index,
    )

    tasks_run = 0
    for i in range(state.current_task_index, len(tasks)):
        log.info(
            "campaign_task_started",
            campaign=campaign_dir.name,
            stage=stage,
            index=i,
            total=len(tasks),
        )
        ok, reason = _execute_task(tasks[i], campaign_dir)
        if not ok:
            failure = reason or f"task {i} failed"
            log.error(
                "campaign_task_failed",
                campaign=campaign_dir.name,
                stage=stage,
                index=i,
                error=failure,
            )
            return _finalize_failure(state_file, state, stage, now, failure)
        tasks_run += 1
        state.current_task_index = i + 1
        write_json(state_file, state.model_dump(), atomic=True)

    return _finalize_success(
        state_file, state, stage, now, tasks_run,
        items_to_promote, published_dir, campaign_dir.name,
    )


def _finalize_failure(
    state_file: Path,
    state: CampaignState,
    stage: Stage,
    now: datetime,
    reason: str,
) -> CampaignRunResult:
    """Record failure in state.history[]; preserve ``current_task_index``."""
    state.last_run = now.isoformat()
    state.history.append({
        "timestamp": now.isoformat(),
        "status": "error",
        "stage": stage,
        "failed_at_task": state.current_task_index,
    })
    write_json(state_file, state.model_dump(), atomic=True)
    return CampaignRunResult(False, state.current_task_index, reason, None)


def _finalize_success(
    state_file: Path,
    state: CampaignState,
    stage: Stage,
    now: datetime,
    tasks_run: int,
    items_to_promote: list[Path],
    published_dir: Path,
    campaign_name: str,
) -> CampaignRunResult:
    """Record full-stage success and (publish only) promote ready/."""
    published_folder: str | None = None
    if stage == "publish" and items_to_promote:
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        target_dir = published_dir / timestamp
        target_dir.mkdir(parents=True, exist_ok=True)
        for item in items_to_promote:
            if item.exists():
                shutil.move(str(item), str(target_dir / item.name))
        published_folder = target_dir.name
        log.info(
            "campaign_promoted_ready",
            campaign=campaign_name,
            count=len(items_to_promote),
            published_folder=published_folder,
        )

    state.last_run = now.isoformat()
    state.current_task_index = 0
    entry: dict[str, str | int] = {
        "timestamp": now.isoformat(),
        "status": "success",
        "stage": stage,
    }
    if published_folder is not None:
        entry["published_folder"] = published_folder
    state.history.append(dict(entry))
    write_json(state_file, state.model_dump(), atomic=True)
    log.info(
        "campaign_run_succeeded",
        campaign=campaign_name,
        stage=stage,
        tasks_run=tasks_run,
        published_folder=published_folder,
    )
    return CampaignRunResult(True, tasks_run, None, published_folder)
