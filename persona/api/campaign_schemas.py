# pyright: reportMissingImports=false
"""Pydantic models for the campaign flow."""

from __future__ import annotations

import warnings
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ScheduleConfig(BaseModel):
    """Schedule for publishing a campaign."""
    cron: str = Field(..., description="A standard cron expression (e.g., '0 10 * * *').")


class GenericTask(BaseModel):
    """A generic platform publishing task."""
    type: Literal["generic"]
    platform: str
    action: str
    params: dict[str, Any] = Field(default_factory=dict)


class CustomHookTask(BaseModel):
    """A task that calls a custom python script/function."""
    type: Literal["custom_hook"]
    script_path: str
    function: str
    params: dict[str, Any] = Field(default_factory=dict)


CampaignTask = GenericTask | CustomHookTask


class CampaignConfig(BaseModel):
    """Configuration for a generalized campaign."""
    schedule: ScheduleConfig
    prepare_tasks: list[CampaignTask] = Field(default_factory=list)
    publish_tasks: list[CampaignTask] = Field(default_factory=list)
    tasks: list[CampaignTask] = Field(
        default_factory=list,
        description="DEPRECATED — use publish_tasks. Auto-migrated by validator.",
    )

    @model_validator(mode="after")
    def _migrate_legacy_tasks(self) -> CampaignConfig:
        """Back-compat: migrate legacy ``tasks`` into ``publish_tasks``.

        - If only ``tasks`` is set, copy into ``publish_tasks`` and warn once.
        - If both are set, raise — caller must remove the legacy field.
        """
        if self.tasks and self.publish_tasks:
            raise ValueError(
                "CampaignConfig has both 'tasks' (legacy) and "
                "'publish_tasks' (current) set; remove 'tasks'."
            )
        if self.tasks and not self.publish_tasks:
            warnings.warn(
                "CampaignConfig field 'tasks' is deprecated; "
                "use 'publish_tasks' instead. Auto-migrating.",
                DeprecationWarning,
                stacklevel=2,
            )
            self.publish_tasks = list(self.tasks)
        return self


class CampaignState(BaseModel):
    """Internal state tracking for a campaign."""
    last_run: str | None = None
    current_task_index: int = 0
    history: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "CampaignConfig",
    "CampaignState",
    "CampaignTask",
    "CustomHookTask",
    "GenericTask",
    "ScheduleConfig",
]
