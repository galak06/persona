# pyright: reportMissingImports=false
"""Pydantic models for the campaign flow."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class ScheduleConfig(BaseModel):
    """Schedule for publishing a campaign."""
    cron: str = Field(..., description="A standard cron expression (e.g., '0 10 * * *').")


class GenericTask(BaseModel):
    """A generic platform publishing task."""
    type: Literal["generic"]
    platform: str
    action: str
    params: Dict[str, Any] = Field(default_factory=dict)


class CustomHookTask(BaseModel):
    """A task that calls a custom python script/function."""
    type: Literal["custom_hook"]
    script_path: str
    function: str
    params: Dict[str, Any] = Field(default_factory=dict)


CampaignTask = Union[GenericTask, CustomHookTask]


class CampaignConfig(BaseModel):
    """Configuration for a generalized campaign."""
    schedule: ScheduleConfig
    tasks: List[CampaignTask]

class CampaignState(BaseModel):
    """Internal state tracking for a campaign."""
    last_run: Optional[str] = None
    current_task_index: int = 0
    history: List[Dict[str, Any]] = Field(default_factory=list)

__all__ = [
    "ScheduleConfig",
    "GenericTask",
    "CustomHookTask",
    "CampaignTask",
    "CampaignConfig",
    "CampaignState"
]
