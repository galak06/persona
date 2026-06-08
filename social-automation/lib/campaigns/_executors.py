"""Task executors used by :func:`lib.campaigns.runner.run_campaign`.

Two task types from ``api.campaign_schemas``:
    - :class:`GenericTask` — placeholder stub from the original worker.
    - :class:`CustomHookTask` — load a python script by path, call a function.

Calling convention preserved verbatim from the original worker:
    - ``task.function == "main"``: ``func(**params)``
      (CLI entry point — does not want ``campaign_dir``).
    - Otherwise: ``func(campaign_dir, **params)``.

Truthy return → success; ``None`` also counts as success.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from api.campaign_schemas import CampaignTask, CustomHookTask, GenericTask
from lib.observability.logger import get_logger

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def execute(task: CampaignTask, campaign_dir: Path) -> tuple[bool, str | None]:
    """Dispatch a task to its executor. Returns (success, error_reason)."""
    if isinstance(task, CustomHookTask):
        return _execute_custom_hook(task, campaign_dir)
    return _execute_generic(task)


def _execute_generic(task: GenericTask) -> tuple[bool, str | None]:
    """Stub matching original ``_execute_generic_task``: log + return True."""
    log.info("generic_task_invoked", platform=task.platform, action=task.action)
    log.warning("generic_task_unimplemented", platform=task.platform, action=task.action)
    return True, None


def _execute_custom_hook(
    task: CustomHookTask,
    campaign_dir: Path,
) -> tuple[bool, str | None]:
    script_p = Path(task.script_path)
    if not script_p.is_absolute():
        script_p = _PROJECT_ROOT / script_p
    if not script_p.exists():
        return False, f"custom hook script not found: {script_p}"

    module_name = f"custom_hook_{campaign_dir.name}_{script_p.stem}"
    spec = importlib.util.spec_from_file_location(module_name, script_p)
    if spec is None or spec.loader is None:
        return False, f"could not load module spec for {script_p}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 — surface hook import errors verbatim
        log.exception("custom_hook_import_failed", script=str(script_p), error=str(exc))
        return False, f"import error in {script_p}: {exc}"

    func = getattr(module, task.function, None)
    if not callable(func):
        return False, f"function {task.function!r} not callable in {script_p}"
    try:
        if task.function == "main":
            # CLI entry points often use argparse, which reads sys.argv.
            # We isolate them from the parent's CLI args (e.g. --campaign).
            old_argv = sys.argv
            sys.argv = [str(script_p)]
            try:
                result = func(**task.params)
            finally:
                sys.argv = old_argv
        else:
            result = func(campaign_dir, **task.params)
    except Exception as exc:  # noqa: BLE001 — surface hook execution errors
        log.exception(
            "custom_hook_raised",
            script=str(script_p),
            function=task.function,
            error=str(exc),
        )
        return False, f"hook {task.function} raised: {exc}"

    if result is None:
        return True, None
    if task.function == "main":
        # For main functions, 0 is success, non-zero is failure
        if result == 0:
            return True, None
        return False, f"hook {task.function} returned exit code {result}"
    if bool(result):
        return True, None
    return False, f"hook {task.function} returned falsy"
