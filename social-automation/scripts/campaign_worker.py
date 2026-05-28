#!/usr/bin/env python3
"""Background worker that dispatches scheduled campaigns.

Thin scheduler: iterates every campaign under ``settings.paths.campaigns_dir``,
evaluates its cron against ``state.json:last_run``, and — when due —
delegates per-campaign execution (lock, tasks, ``ready/`` → ``published/``,
``state.json`` mutation) to :func:`lib.campaigns.run_campaign`. Failures
notify Telegram. Cron behaviour preserved verbatim.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from croniter import croniter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.bootstrap import init_script

settings, logger = init_script(__name__)

from api.campaign_schemas import CampaignConfig, CampaignState
from lib.campaigns import LockHeldError, run_campaign


def _should_run(cron_expr: str, last_run_iso: str | None, now: datetime) -> bool:
    if not last_run_iso:
        return True
    try:
        last_run = datetime.fromisoformat(last_run_iso)
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("Invalid last_run format: %s, running immediately", last_run_iso)
        return True
    try:
        next_run = croniter(cron_expr, last_run).get_next(datetime)
        return bool(next_run <= now)
    except Exception as e:
        logger.error("Error evaluating cron expression '%s': %s", cron_expr, e)
        return False


def _notify_telegram_failure(campaign_name: str, error: str) -> None:
    try:
        import notifier
        notifier.send(
            f"❌ Campaign worker failed for <b>{campaign_name}</b>.\n{error}",
            silent=False,
        )
    except Exception as e:
        logger.error("Failed to send telegram notification: %s", e)


def _load_campaign(campaign_dir: Path) -> tuple[CampaignConfig, CampaignState] | None:
    config_file = campaign_dir / "campaign_config.json"
    state_file = campaign_dir / "state.json"
    if not config_file.exists():
        return None
    try:
        config = CampaignConfig(**json.loads(config_file.read_text(encoding="utf-8")))
    except Exception as e:
        logger.error("Invalid config in %s: %s", config_file, e)
        return None
    state = CampaignState()
    if state_file.exists():
        try:
            loaded = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = CampaignState.model_validate(loaded)
        except json.JSONDecodeError:
            logger.warning("Failed to decode state.json for %s", campaign_dir.name)
    return config, state


def main() -> None:
    argparse.ArgumentParser(description="Run scheduled campaigns").parse_args()
    if settings.paths is None:
        logger.error("settings.paths is not configured; aborting campaign worker.")
        return
    campaigns_root = settings.paths.campaigns_dir
    if not campaigns_root.exists():
        logger.warning("Campaigns root %s does not exist.", campaigns_root)
        return
    now = datetime.now(timezone.utc)
    for campaign_dir in sorted(campaigns_root.iterdir()):
        if not campaign_dir.is_dir():
            continue
        loaded = _load_campaign(campaign_dir)
        if loaded is None:
            continue
        config, state = loaded
        if not _should_run(config.schedule.cron, state.last_run, now):
            continue
        logger.info("Starting run for campaign: %s", campaign_dir.name)
        try:
            result = run_campaign(campaign_dir, stage="publish")
        except LockHeldError:
            logger.info("Campaign %s is currently locked. Skipping.", campaign_dir.name)
            continue
        except Exception as e:
            logger.exception("Campaign %s raised an unhandled error", campaign_dir.name)
            _notify_telegram_failure(campaign_dir.name, str(e))
            continue
        if not result.ok:
            _notify_telegram_failure(campaign_dir.name, result.error or "unknown")


if __name__ == "__main__":
    main()
