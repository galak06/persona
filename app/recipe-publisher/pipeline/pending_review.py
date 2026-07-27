# pyright: reportMissingImports=false
"""Phase 4 of the recipe-publisher pipeline: pending review.

Stages generated drafts into the review queue: recipes with complete
``generated_content`` move ``content_status`` from ``generated`` to ``pending``.
Incomplete drafts are left in ``generated`` (and counted) rather than staged,
so the human reviewer only ever sees complete drafts.

Run::

    python -m pipeline.pending_review [--dry-run] [--health-check]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from recipe_db import db
from recipe_db.models import ContentStatus
from recipe_db.repository import RecipeRepository

from pipeline._cli import get_phase_logger
from pipeline.checkpoint import StructuredLogger, checkpoint

PHASE = "pending_review"
_REQUIRED = ("title", "body_markdown", "ig_caption")


@dataclass(frozen=True)
class ReviewStageReport:
    """Outcome of one pending-review staging run."""

    candidates: int
    staged: int
    incomplete: int


class ReviewStager:
    """Promotes complete generated drafts to the ``pending`` review state."""

    def __init__(
        self,
        repo: RecipeRepository,
        *,
        logger: StructuredLogger | None = None,
    ) -> None:
        self._repo = repo
        self._log = logger

    def run(self, *, persist: bool) -> ReviewStageReport:
        rows = self._repo.list_by_content_status(ContentStatus.GENERATED)
        staged = 0
        incomplete = 0
        for row in rows:
            if all(row.generated_content.get(field) for field in _REQUIRED):
                if persist:
                    self._repo.set_content_status(row.id, ContentStatus.PENDING)
                staged += 1
            else:
                incomplete += 1
        report = ReviewStageReport(
            candidates=len(rows), staged=staged, incomplete=incomplete
        )
        self._gate(report)
        return report

    def _gate(self, report: ReviewStageReport) -> None:
        """Invariant: staged + incomplete account for every candidate."""
        ok = report.staged + report.incomplete == report.candidates
        checkpoint(
            PHASE,
            ok=ok,
            reason="" if ok else "staging accounting mismatch",
            logger=self._log,
            candidates=report.candidates,
            staged=report.staged,
            incomplete=report.incomplete,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pending-review staging phase (recipe pipeline phase 4)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report stageable drafts without changing status",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="verify the recipe DB is reachable, then exit",
    )
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
        report = ReviewStager(RecipeRepository(conn), logger=log).run(
            persist=not args.dry_run
        )
    finally:
        conn.close()

    log.info(
        "pending_review_done",
        candidates=report.candidates,
        staged=report.staged,
        incomplete=report.incomplete,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
