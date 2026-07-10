"""Cron due-check: should a scheduled task run again right now?

Extracted from `scripts/campaign_worker.py`'s inline `_should_run` helper so
`scripts/task_dispatcher.py` (Phase A's Postgres+Redis dispatcher) can share
the exact same due-check semantics without duplicating the croniter dance.
`campaign_worker.py` itself keeps its own private copy unchanged -- it is a
separate, unrelated system (dogfoodandfun's campaign/recipe pipeline) that
this stage does not touch -- so this module has no import-time relationship
to it; the logic is simply lifted verbatim.
"""

from __future__ import annotations

from datetime import UTC, datetime

from croniter import croniter

from lib.observability import get_logger

logger = get_logger(__name__)


def is_task_due(cron_expr: str, last_run_iso: str | None, now: datetime) -> bool:
    """True if `cron_expr` has a scheduled fire time in (`last_run_iso`, `now`].

    No prior run (`last_run_iso` falsy) always counts as due -- a freshly
    seeded task should fire on its first due-check, not wait a full cron
    period. A malformed `last_run_iso` or `cron_expr` fails open/closed the
    same way `campaign_worker.py`'s original `_should_run` does: an
    unparsable timestamp is treated as "no valid prior run" (due=True) so a
    corrupt value can't permanently wedge a task; an unparsable cron
    expression is treated as "not due" (due=False) so a bad config doesn't
    fire uncontrollably.
    """
    if not last_run_iso:
        return True
    try:
        last_run = datetime.fromisoformat(last_run_iso)
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=UTC)
    except ValueError:
        logger.warning("invalid_last_run_format", last_run_iso=last_run_iso)
        return True
    try:
        next_run = croniter(cron_expr, last_run).get_next(datetime)
        return bool(next_run <= now)
    except Exception as exc:
        logger.error("invalid_cron_expression", cron_expr=cron_expr, error=str(exc))
        return False
