# pyright: reportMissingImports=false
"""FastAPI router for campaign management.

Thin HTTP wrappers over the on-disk campaigns directory + the manual
``scripts.publish_campaign`` CLI:

- ``GET  /api/v1/campaigns``                 → list summaries
- ``GET  /api/v1/campaigns/{name}``          → single detail + history
- ``POST /api/v1/campaigns/{name}/publish``  → spawn detached publisher

The POST handler uses a fire-and-forget ``Popen`` (option A in the design
doc): we cannot synchronously distinguish a lock conflict from a normal
slow start without blocking, and the frontend polls ``GET /campaigns``
anyway — a failed run shows up as ``last_status="error"`` in the next
poll cycle, which is the same surface the cron path uses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi import Path as FastApiPath
from pydantic import ValidationError

from api.campaign_schemas import CampaignConfig
from api.schemas import (
    CampaignDetail,
    CampaignListResponse,
    CampaignStatusLiteral,
    CampaignSummary,
    TriggerResponse,
)
from lib.config import settings
from lib.observability import get_logger

router = APIRouter()
_log = get_logger(__name__)

_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"


def _campaigns_dir() -> Path:
    """Resolve the campaigns root, raising 500 if BRAND_DIR is unbound."""
    if settings.paths is None:
        raise HTTPException(
            status_code=500,
            detail="settings.paths is unset; BRAND_DIR not resolved",
        )
    return settings.paths.campaigns_dir


def _safe_count(folder: Path) -> int:
    """Count direct children of ``folder``; tolerate missing folder."""
    if not folder.is_dir():
        return 0
    try:
        return sum(1 for _ in folder.iterdir())
    except OSError:
        return 0


def _load_state(state_path: Path) -> dict[str, Any]:
    """Read state.json best-effort. Returns ``{}`` on missing/corrupt."""
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("campaign_state_unreadable", path=str(state_path), error=str(exc))
        return {}


def _parse_last_run(raw: Any) -> datetime | None:
    """Coerce ISO-8601 strings (possibly with trailing ``Z``) to datetime."""
    if not isinstance(raw, str) or not raw:
        return None
    candidate = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _last_status(history: list[dict[str, Any]]) -> CampaignStatusLiteral:
    tail = history[-1].get("status") if history else None
    if tail == "success":
        return "success"
    return "error" if tail == "error" else "never"


def _load_config(config_path: Path) -> CampaignConfig | None:
    """Parse campaign_config.json. Returns None on any failure."""
    if not config_path.exists():
        return None
    try:
        return CampaignConfig(**json.loads(config_path.read_text()))
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        _log.warning("campaign_config_invalid", path=str(config_path), error=str(exc))
        return None


def _build_summary(campaign_dir: Path, cfg: CampaignConfig) -> CampaignSummary:
    state = _load_state(campaign_dir / "state.json")
    history_raw = state.get("history") or []
    history: list[dict[str, Any]] = [h for h in history_raw if isinstance(h, dict)]
    return CampaignSummary(
        name=campaign_dir.name,
        last_run=_parse_last_run(state.get("last_run")),
        current_task_index=int(state.get("current_task_index") or 0),
        last_status=_last_status(history),
        ready_count=_safe_count(campaign_dir / "ready"),
        published_count=_safe_count(campaign_dir / "published"),
        has_prepare_tasks=len(cfg.prepare_tasks) > 0,
        has_publish_tasks=len(cfg.publish_tasks) > 0,
    )


@router.get("", response_model=CampaignListResponse)
def list_campaigns() -> CampaignListResponse:
    """Return one summary per folder under campaigns_dir that has a config.

    Folders without ``campaign_config.json`` are silently skipped — the
    UI only renders runnable campaigns. Invalid configs are also skipped
    (logged at WARN) so a single broken JSON file can't 500 the list.
    """
    root = _campaigns_dir()
    summaries: list[CampaignSummary] = []
    if not root.is_dir():
        _log.info("campaigns_dir_missing", path=str(root))
        return CampaignListResponse(campaigns=summaries)
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        cfg = _load_config(child / "campaign_config.json")
        if cfg is None:
            continue
        summaries.append(_build_summary(child, cfg))
    _log.info("campaigns_listed", count=len(summaries))
    return CampaignListResponse(campaigns=summaries)


@router.get("/{name}", response_model=CampaignDetail)
def get_campaign(
    name: str = FastApiPath(..., pattern=_NAME_PATTERN),
) -> CampaignDetail:
    """Return full detail (summary + history) for a single campaign."""
    campaign_dir = _campaigns_dir() / name
    cfg = _load_config(campaign_dir / "campaign_config.json")
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"campaign not found: {name}")
    summary = _build_summary(campaign_dir, cfg)
    state = _load_state(campaign_dir / "state.json")
    history_raw = state.get("history") or []
    history: list[dict[str, Any]] = [h for h in history_raw if isinstance(h, dict)]
    return CampaignDetail(**summary.model_dump(), history=history)


@router.post("/{name}/publish", response_model=TriggerResponse)
def publish_campaign(
    name: str = FastApiPath(..., pattern=_NAME_PATTERN),
) -> TriggerResponse:
    """Spawn ``scripts.publish_campaign`` as a detached subprocess.

    Fire-and-forget by design: the publisher writes to ``state.json`` and
    the frontend polls ``GET /campaigns``. Lock conflicts surface there
    as ``last_status="error"`` on the next poll, identical to the cron
    path. Returns immediately with the spawned PID.
    """
    campaign_dir = _campaigns_dir() / name
    if _load_config(campaign_dir / "campaign_config.json") is None:
        raise HTTPException(status_code=404, detail=f"campaign not found: {name}")
    proc = subprocess.Popen(  # noqa: S603 - args are list-form, name is regex-validated
        [sys.executable, "-m", "scripts.publish_campaign", "--campaign", name],
        cwd=str(_PROJECT_ROOT),
        env={**os.environ},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    _log.info("campaign_publish_spawned", campaign=name, pid=proc.pid)
    return TriggerResponse(
        ok=True,
        message=f"publish_campaign spawned (pid={proc.pid})",
        label=f"campaigns/{name}/publish",
    )


__all__ = ["router"]
