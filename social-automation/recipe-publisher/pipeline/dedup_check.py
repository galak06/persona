# pyright: reportMissingImports=false
"""Phase 6 of the recipe-publisher pipeline: dedup check.

Flags approved recipes that duplicate an already-published recipe — by id
(title slug) or by ``content_hash`` — so the publishing phase never reposts the
same recipe. With ``--dry-run`` it only reports; otherwise duplicates are moved
to ``rejected`` so they drop out of the publish queue.

Note: ``lib.deduplication`` is intentionally NOT reused here — it dedups
*engagement posts* by (platform, post_id) against a global cache, a different
domain from recipe-republish dedup, which keys off the recipe DB.

Run::

    python -m pipeline.dedup_check [--dry-run] [--health-check]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from recipe_db import db
from recipe_db.models import ContentStatus
from recipe_db.repository import RecipeRepository

from pipeline._cli import get_phase_logger
from pipeline.checkpoint import StructuredLogger, checkpoint

PHASE = "dedup_check"


def _load_published_slugs() -> set[str]:
    """Read already-published slugs from state/published_recipes.json (if any)."""
    from pathlib import Path

    from lib.io.jsonio import read_json

    path = Path(__file__).resolve().parent.parent / "state" / "published_recipes.json"
    history = read_json(path, [])
    if not isinstance(history, list):
        return set()
    return {
        entry["slug"]
        for entry in history
        if isinstance(entry, dict) and entry.get("slug")
    }


@dataclass(frozen=True)
class DedupReport:
    """Outcome of one dedup-check run."""

    candidates: int
    unique: int
    duplicates: int
    unique_ids: list[str] = field(default_factory=list)


class DedupChecker:
    """Separates approved recipes into unique vs already-published duplicates."""

    def __init__(
        self,
        repo: RecipeRepository,
        *,
        published_slugs: set[str] | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        self._repo = repo
        # Externally-recorded already-published slugs (e.g. from
        # state/published_recipes.json). Within the recipes table id (PK) and
        # content_hash (UNIQUE) can't collide across rows, so cross-run dedup
        # must key off this external history, not the table's own unique keys.
        self._published_slugs = set(published_slugs or ())
        self._log = logger

    def run(self, *, persist: bool) -> DedupReport:
        already_published = self._already_published_ids()
        approved = self._repo.list_by_content_status(ContentStatus.APPROVED)
        unique_ids: list[str] = []
        duplicates = 0
        for row in approved:
            if row.id in already_published:
                duplicates += 1
                if persist:
                    self._repo.set_content_status(row.id, ContentStatus.REJECTED)
            else:
                unique_ids.append(row.id)
        report = DedupReport(
            candidates=len(approved),
            unique=len(unique_ids),
            duplicates=duplicates,
            unique_ids=unique_ids,
        )
        self._gate(report)
        return report

    def _already_published_ids(self) -> set[str]:
        """External published slugs ∪ recipes already in the PUBLISHED state."""
        db_published = {
            r.id
            for r in self._repo.list_by_content_status(ContentStatus.PUBLISHED)
        }
        return self._published_slugs | db_published

    def _gate(self, report: DedupReport) -> None:
        ok = report.unique + report.duplicates == report.candidates
        checkpoint(
            PHASE,
            ok=ok,
            reason="" if ok else "dedup accounting mismatch",
            logger=self._log,
            candidates=report.candidates,
            unique=report.unique,
            duplicates=report.duplicates,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dedup-check phase (recipe pipeline phase 6)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report duplicates without rejecting them",
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
        report = DedupChecker(
            RecipeRepository(conn),
            published_slugs=_load_published_slugs(),
            logger=log,
        ).run(persist=not args.dry_run)
    finally:
        conn.close()

    log.info(
        "dedup_check_done",
        candidates=report.candidates,
        unique=report.unique,
        duplicates=report.duplicates,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
