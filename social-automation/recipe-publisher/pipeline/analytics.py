# pyright: reportMissingImports=false
"""Phase 10 of the recipe-publisher pipeline: analytics tracking.

Aggregates the per-attempt publish outcomes recorded across every recipe's
``publish_results`` (the local outcome log) into platform/status rollups. This
is the "local outcome log only" analytics chosen for the pipeline — no external
metric pulls.

Run::

    python -m pipeline.analytics [--health-check]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from recipe_db import db
from recipe_db.repository import RecipeRepository

from pipeline._cli import get_phase_logger
from pipeline.checkpoint import StructuredLogger, checkpoint

PHASE = "analytics"


@dataclass(frozen=True)
class AnalyticsReport:
    """Aggregated publish outcomes across all recipes."""

    recipes: int
    attempts: int
    by_platform: dict[str, dict[str, int]] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)


class AnalyticsTracker:
    """Rolls up ``publish_results`` into platform/status counts."""

    def __init__(
        self,
        repo: RecipeRepository,
        *,
        logger: StructuredLogger | None = None,
    ) -> None:
        self._repo = repo
        self._log = logger

    def run(self) -> AnalyticsReport:
        rows = self._repo.list_recipes()
        by_platform: dict[str, dict[str, int]] = {}
        by_status: dict[str, int] = {}
        attempts = 0
        for row in rows:
            for res in row.publish_results:
                attempts += 1
                platform = res.get("platform", "unknown")
                status = res.get("status", "unknown")
                by_platform.setdefault(platform, {})
                by_platform[platform][status] = (
                    by_platform[platform].get(status, 0) + 1
                )
                by_status[status] = by_status.get(status, 0) + 1
        report = AnalyticsReport(
            recipes=len(rows),
            attempts=attempts,
            by_platform=by_platform,
            by_status=by_status,
        )
        self._gate(report)
        return report

    def _gate(self, report: AnalyticsReport) -> None:
        """Invariant: status counts reconcile with the total attempt count."""
        ok = sum(report.by_status.values()) == report.attempts
        checkpoint(
            PHASE,
            ok=ok,
            reason="" if ok else "status counts do not reconcile with attempts",
            logger=self._log,
            recipes=report.recipes,
            attempts=report.attempts,
            statuses=len(report.by_status),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analytics tracking phase (recipe pipeline phase 10)."
    )
    parser.add_argument("--health-check", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    log = get_phase_logger(PHASE, args.log_level)

    if args.health_check:
        conn = db.connect()
        conn.execute("PRAGMA schema_version")
        conn.close()
        return 0

    conn = db.connect()
    try:
        db.migrate(conn)
        report = AnalyticsTracker(RecipeRepository(conn), logger=log).run()
    finally:
        conn.close()

    log.info(
        "analytics_done",
        recipes=report.recipes,
        attempts=report.attempts,
        by_status=report.by_status,
        by_platform=report.by_platform,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
