"""Manual trigger for one campaign's publish stage.

Counterpart to ``POST /api/v1/campaigns/{name}/publish``. Runs the
``publish`` stage of a single campaign under
``settings.paths.campaigns_dir`` via :func:`lib.campaigns.run_campaign`.

The per-campaign ``worker.lock`` (acquired inside ``run_campaign``)
prevents collision with the cron worker — there is no script-level
:class:`SingletonLock` here.

Usage::

    python -m scripts.publish_campaign --campaign recipes
    python -m scripts.publish_campaign --campaign recipes --dry-run
    python -m scripts.publish_campaign --campaign recipes --health-check

Exit codes:
    0 — success (or dry-run / health-check ok)
    1 — campaign not found, config broken, or stage failed
    2 — worker.lock held by another process (API maps to HTTP 409)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from api.campaign_schemas import CampaignConfig

from lib.campaigns import LockHeldError, run_campaign
from lib.config import settings
from lib.observability import get_logger

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_LOCKED = 2

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Manual trigger for one campaign's publish stage. "
            "Counterpart to POST /api/v1/campaigns/{name}/publish."
        ),
    )
    parser.add_argument(
        "--campaign",
        required=True,
        help="Campaign folder name under settings.paths.campaigns_dir.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List planned tasks without mutating state.",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Verify campaign config + hook script paths and exit.",
    )
    return parser.parse_args()


def _campaign_dir(campaign: str) -> Path:
    if settings.paths is None:
        raise RuntimeError("settings.paths is unset; lib.config failed to resolve BRAND_DIR")
    return settings.paths.campaigns_dir / campaign


def _resolve_hook_path(script_path: str) -> Path:
    path = Path(script_path)
    return path if path.is_absolute() else _PROJECT_ROOT / path


def _health_check(campaign: str) -> int:
    """Validate config exists, parses, and every custom_hook script_path resolves."""
    campaign_dir = _campaign_dir(campaign)
    config_path = campaign_dir / "campaign_config.json"
    if not config_path.exists():
        print(f"CAMPAIGN_BROKEN: {config_path} missing", file=sys.stderr)
        return EXIT_FAIL
    try:
        cfg = CampaignConfig(**json.loads(config_path.read_text()))
    except Exception as exc:
        print(f"CAMPAIGN_BROKEN: config parse failed: {exc}", file=sys.stderr)
        return EXIT_FAIL

    for task in (*cfg.prepare_tasks, *cfg.publish_tasks):
        if task.type != "custom_hook":
            continue
        resolved = _resolve_hook_path(task.script_path)
        if not resolved.exists():
            print(
                f"CAMPAIGN_BROKEN: hook missing: {task.script_path}",
                file=sys.stderr,
            )
            return EXIT_FAIL

    print(f"Campaign OK (config: {config_path})")
    return EXIT_OK


def main(*, campaign: str, dry_run: bool = False) -> int:
    log = get_logger(__name__)
    campaign_dir = _campaign_dir(campaign)
    config_path = campaign_dir / "campaign_config.json"
    if not config_path.exists():
        print(f"CAMPAIGN_NOT_FOUND: {campaign_dir}", file=sys.stderr)
        log.error(
            "campaign_not_found",
            campaign=campaign,
            dir=str(campaign_dir),
        )
        return EXIT_FAIL

    try:
        result = run_campaign(campaign_dir, stage="publish", dry_run=dry_run)
    except LockHeldError as exc:
        print(f"CAMPAIGN_LOCKED: {exc}", file=sys.stderr)
        log.info("campaign_locked", campaign=campaign)
        return EXIT_LOCKED

    if not result.ok:
        print(f"CAMPAIGN_FAILED: {result.error}", file=sys.stderr)
        log.error(
            "campaign_failed",
            campaign=campaign,
            tasks_run=result.tasks_run,
            error=result.error,
        )
        return EXIT_FAIL

    print(
        "campaign ok: "
        f"tasks_run={result.tasks_run} "
        f"published_folder={result.published_folder}",
    )
    log.info(
        "campaign_cli_succeeded",
        campaign=campaign,
        dry_run=dry_run,
        tasks_run=result.tasks_run,
        published_folder=result.published_folder,
    )
    return EXIT_OK


if __name__ == "__main__":
    args = _parse_args()
    if args.health_check:
        sys.exit(_health_check(args.campaign))
    sys.exit(main(campaign=args.campaign, dry_run=args.dry_run))
