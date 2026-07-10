"""Scheduling helpers — re-run guards, time-window gates.

Replaces 6 inline reimplementations of `last_run.json` checking
across `scripts/*.py`.
"""

from lib.scheduling.cron_due import is_task_due
from lib.scheduling.once_per_window import (
    AlreadyRanError,
    last_run_status,
    once_per,
    record_run,
)

__all__ = [
    "AlreadyRanError",
    "is_task_due",
    "last_run_status",
    "once_per",
    "record_run",
]
