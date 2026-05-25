#!/usr/bin/env python3
"""Background worker to execute generic campaigns based on cron schedules.

Iterates over all campaigns in settings.paths.campaigns_dir. For each campaign:
1. Reads `campaign_config.json` and `state.json`.
2. Evaluates the cron schedule against the last run time.
3. If it's time to run AND the `ready/` folder is not empty, executes the defined tasks.
4. On success, moves the contents of `ready/` to `published/` and updates `state.json`.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from croniter import croniter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.bootstrap import init_script

settings, logger = init_script(__name__)

from api.campaign_schemas import CampaignConfig, CampaignState, CustomHookTask, GenericTask


def _load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Failed to decode JSON from %s", path)
        return default


def _save_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _should_run(cron_expr: str, last_run_iso: str | None, now: datetime) -> bool:
    if not last_run_iso:
        # If it has never run, run it immediately
        return True

    try:
        last_run = datetime.fromisoformat(last_run_iso)
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("Invalid last_run format: %s, running immediately", last_run_iso)
        return True

    # Use croniter to find the next scheduled run after the last run
    try:
        itr = croniter(cron_expr, last_run)
        next_run = itr.get_next(datetime)
        
        # If the next scheduled run is in the past or now, we should run
        return next_run <= now
    except Exception as e:
        logger.error("Error evaluating cron expression '%s': %s", cron_expr, e)
        return False


def _execute_generic_task(task: GenericTask, campaign_dir: Path) -> bool:
    logger.info("Executing generic task: platform=%s, action=%s", task.platform, task.action)
    # Placeholder for generic task routing (e.g., calling Facebook/IG publisher)
    logger.warning("Generic task execution not yet implemented. Assuming success for now.")
    return True


def _execute_custom_hook(task: CustomHookTask, campaign_dir: Path) -> bool:
    logger.info("Executing custom hook: script=%s, function=%s", task.script_path, task.function)
    
    script_p = Path(task.script_path)
    if not script_p.is_absolute():
        script_p = PROJECT_ROOT / script_p

    if not script_p.exists():
        logger.error("Custom hook script not found: %s", script_p)
        return False

    module_name = f"custom_hook_{campaign_dir.name}"
    
    try:
        spec = importlib.util.spec_from_file_location(module_name, script_p)
        if spec is None or spec.loader is None:
            logger.error("Could not load spec for %s", script_p)
            return False
            
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            logger.error("Failed to execute module %s from %s. You may have an import error: %s", module_name, script_p, e)
            return False
            
        func = getattr(module, task.function, None)
        if not func or not callable(func):
            logger.error("Function %s not found or not callable in %s", task.function, script_p)
            return False
            
        # Call the hook function. 
        # If it's a CLI 'main', it likely doesn't want campaign_dir as the first arg.
        # We'll pass it only if the function is not named 'main'.
        if task.function == "main":
            result = func(**task.params)
        else:
            result = func(campaign_dir, **task.params)
        return bool(result) if result is not None else True
        
    except Exception as e:
        logger.exception("Error executing custom hook %s in %s: %s", task.function, script_p, e)
        return False


def process_campaign(campaign_dir: Path, now: datetime) -> None:
    logger.debug("Checking campaign: %s", campaign_dir.name)
    
    config_file = campaign_dir / "campaign_config.json"
    state_file = campaign_dir / "state.json"
    
    ready_dir = campaign_dir / "ready"
    published_dir = campaign_dir / "published"
    
    if not config_file.exists():
        return
        
    try:
        config_data = _load_json(config_file, {})
        config = CampaignConfig(**config_data)
    except Exception as e:
        logger.error("Invalid config in %s: %s", config_file, e)
        return
        
    state_data = _load_json(state_file, {})
    state = CampaignState(**state_data)
    
    if not _should_run(config.schedule.cron, state.last_run, now):
        return
        
    if not ready_dir.exists() or not any(ready_dir.iterdir()):
        logger.info("Campaign %s is scheduled but `ready/` is empty.", campaign_dir.name)
        return
        
    lock_file = campaign_dir / "worker.lock"
    if lock_file.exists():
        try:
            lock_time = datetime.fromtimestamp(lock_file.stat().st_mtime, tz=timezone.utc)
            if (now - lock_time).total_seconds() < 3600:
                logger.info("Campaign %s is currently locked (running since %s). Skipping.", campaign_dir.name, lock_time.isoformat())
                return
            else:
                logger.warning("Found stale lock file for %s, removing it.", campaign_dir.name)
                lock_file.unlink()
        except Exception as e:
            logger.error("Error checking lock file for %s: %s", campaign_dir.name, e)
            return

    logger.info("Starting run for campaign: %s", campaign_dir.name)
    lock_file.touch()
    
    try:
        # Take a snapshot of files currently in ready/ to avoid moving files dropped during execution
        items_to_move = list(ready_dir.iterdir())
        
        # Execute tasks
        success = True
        for i in range(state.current_task_index, len(config.tasks)):
            task = config.tasks[i]
            logger.info("Running task %d/%d for %s", i + 1, len(config.tasks), campaign_dir.name)
            
            if isinstance(task, GenericTask):
                task_success = _execute_generic_task(task, campaign_dir)
            elif isinstance(task, CustomHookTask):
                task_success = _execute_custom_hook(task, campaign_dir)
            else:
                logger.error("Unknown task type: %s", type(task))
                task_success = False
                
            if not task_success:
                logger.error("Task %d failed, aborting campaign run. Will resume from here next time.", i)
                success = False
                break
            else:
                # Successfully completed this task, increment pointer and save state immediately
                state.current_task_index = i + 1
                _save_json(state_file, state.model_dump())
                
        if success:
            # Move files to a timestamped folder in published
            timestamp = now.strftime("%Y%m%d_%H%M%S")
            target_dir = published_dir / timestamp
            target_dir.mkdir(parents=True, exist_ok=True)
            
            for item in items_to_move:
                if item.exists():  # Ensure it hasn't been deleted during execution
                    if item.is_file():
                        shutil.move(str(item), str(target_dir / item.name))
                    elif item.is_dir():
                        shutil.move(str(item), str(target_dir / item.name))
                    
            logger.info("Moved %d ready items to %s", len(items_to_move), target_dir)
            
            # Update state for full completion
            state.last_run = now.isoformat()
            state.current_task_index = 0  # Reset for the next campaign cycle
            state.history.append({
                "timestamp": now.isoformat(),
                "status": "success",
                "published_folder": target_dir.name
            })
            _save_json(state_file, state.model_dump())
            logger.info("Campaign %s run completed successfully.", campaign_dir.name)
        else:
            # Update state on failure to wait for next cron tick instead of infinite retries
            state.last_run = now.isoformat()
            # Do NOT reset current_task_index here, so it resumes properly next time.
            state.history.append({
                "timestamp": now.isoformat(),
                "status": "error",
                "failed_at_task": state.current_task_index
            })
            _save_json(state_file, state.model_dump())
            logger.error("Campaign %s run failed. Updated last_run to prevent immediate retry.", campaign_dir.name)
            
            try:
                import notifier
                notifier.send(f"❌ Campaign worker failed for <b>{campaign_dir.name}</b>.\nCheck logs for details.", silent=False)
            except Exception as e:
                logger.error("Failed to send telegram notification: %s", e)
    finally:
        if lock_file.exists():
            try:
                lock_file.unlink()
            except OSError as e:
                logger.error("Failed to remove lock file for %s: %s", campaign_dir.name, e)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run scheduled campaigns")
    parser.parse_args()
    
    campaigns_root = settings.paths.campaigns_dir
    if not campaigns_root.exists():
        logger.warning("Campaigns root %s does not exist.", campaigns_root)
        return
        
    now = datetime.now(timezone.utc)
    
    for item in campaigns_root.iterdir():
        if item.is_dir():
            process_campaign(item, now)


if __name__ == "__main__":
    main()
