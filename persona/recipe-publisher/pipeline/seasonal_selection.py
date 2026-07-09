# pyright: reportMissingImports=false
"""Phase 1 of the recipe-publisher pipeline: seasonal selection.

Selects recipes appropriate to a target season (default: the current calendar
season for the USA/Canada audience). The phase:

  1. Reads recipes from the recipe DB.
  2. Infers each recipe's seasons from its title/tags/category and, unless
     ``--dry-run``, persists them to the ``recipes.season_tags`` column.
  3. Selects the recipes eligible for the target season.
  4. Runs the end-of-phase checkpoint gate.

Cross-cutting concerns reuse the shared lib: structured JSON logging
(``lib.observability.logger``) and ``--health-check``
(``lib.runtime.health_check``).

Run::

    python -m pipeline.seasonal_selection [--season fall] [--dry-run]
                                          [--health-check]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Bridge: ensure both the recipe-publisher root (for ``recipe_db``/``pipeline``)
# and the social-automation root (for ``lib.*``) are importable regardless of
# the current working directory — mirrors api/recipes_api.py's path handling.
_RECIPE_PUBLISHER = Path(__file__).resolve().parent.parent
_SOCIAL_AUTOMATION = _RECIPE_PUBLISHER.parent
for _root in (_RECIPE_PUBLISHER, _SOCIAL_AUTOMATION):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from recipe_db import db
from recipe_db.repository import RecipeRepository

from pipeline import seasons
from pipeline.checkpoint import StructuredLogger, checkpoint

PHASE = "seasonal_selection"


@dataclass(frozen=True)
class SeasonalSelectionReport:
    """Outcome of one seasonal-selection run."""

    season: str
    total: int
    inferred: int
    persisted: int
    selected_ids: list[str] = field(default_factory=list)

    @property
    def selected(self) -> int:
        return len(self.selected_ids)


class SeasonalSelector:
    """Selects season-appropriate recipes from the recipe DB.

    Stateless apart from the injected repository and logger, so it is trivial
    to unit-test against an in-memory DB with a fake logger.
    """

    def __init__(
        self,
        repo: RecipeRepository,
        *,
        logger: StructuredLogger | None = None,
    ) -> None:
        self._repo = repo
        self._log = logger

    def run(self, *, season: str, persist: bool) -> SeasonalSelectionReport:
        """Infer/select recipes for ``season`` and run the checkpoint gate."""
        target = seasons.normalize_season(season)
        rows = self._repo.list_recipes()
        inferred = 0
        persisted = 0
        selected_ids: list[str] = []
        for row in rows:
            effective = row.season_tags
            if not effective:
                effective = seasons.infer_seasons(
                    row.name, row.tags, row.category
                )
                inferred += 1
                if persist and effective:
                    self._repo.set_season_tags(row.id, effective)
                    persisted += 1
            if seasons.in_season(effective, target):
                selected_ids.append(row.id)
        report = SeasonalSelectionReport(
            season=target,
            total=len(rows),
            inferred=inferred,
            persisted=persisted,
            selected_ids=selected_ids,
        )
        self._gate(report)
        return report

    def _gate(self, report: SeasonalSelectionReport) -> None:
        """End-of-phase invariant: a valid season selecting a subset of rows."""
        ok = (
            report.season in seasons.SEASONS
            and 0 <= report.selected <= report.total
        )
        checkpoint(
            PHASE,
            ok=ok,
            reason="" if ok else "invalid season or selection out of range",
            logger=self._log,
            season=report.season,
            total=report.total,
            inferred=report.inferred,
            persisted=report.persisted,
            selected=report.selected,
        )


def _db_probe():  # type: ignore[no-untyped-def]
    """Health probe: the recipe DB file opens and answers a trivial pragma."""
    from lib.runtime.health_check import HealthCheckResult

    try:
        conn = db.connect()
        try:
            conn.execute("PRAGMA schema_version")
        finally:
            conn.close()
        return HealthCheckResult("recipe_db", True, "recipes.db reachable")
    except Exception as exc:
        return HealthCheckResult(
            "recipe_db", False, f"db error: {type(exc).__name__}"
        )


def _run_health_check() -> int:
    from lib.runtime.health_check import register, run_health_checks

    register("recipe_db", _db_probe)
    return 0 if run_health_checks(["recipe_db"]) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seasonal selection phase (recipe pipeline phase 1)."
    )
    parser.add_argument("--season", help="target season; default = current")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute selection without persisting season_tags",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="verify the recipe DB is reachable, then exit",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    from lib.observability.logger import configure_logging, get_logger

    configure_logging(level=args.log_level)
    log = get_logger(PHASE)

    if args.health_check:
        return _run_health_check()

    target = args.season or seasons.current_season()
    conn = db.connect()
    try:
        db.migrate(conn)
        selector = SeasonalSelector(RecipeRepository(conn), logger=log)
        report = selector.run(season=target, persist=not args.dry_run)
    finally:
        conn.close()

    log.info(
        "seasonal_selection_done",
        season=report.season,
        total=report.total,
        inferred=report.inferred,
        persisted=report.persisted,
        selected=report.selected,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
