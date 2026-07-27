# pyright: reportMissingImports=false
"""Phase 3 of the recipe-publisher pipeline: content generation.

Generates draft post content (title, body, IG caption, image brief) for
dog-safe recipes that have no content yet, stores it on
``recipes.generated_content``, and advances ``content_status`` to ``generated``.

The voice/image producer is injected (``DraftProducer`` protocol) so the phase
is testable without external API calls. The production adapter wraps the
existing seed-grounded ``generators.recipe.generate_recipe``. ``--dry-run``
reports how many recipes are eligible without calling the producer or writing.

Run::

    python -m pipeline.content_generation [--limit N] [--dry-run] [--health-check]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from recipe_db import db
from recipe_db.models import ContentStatus, RecipeRow
from recipe_db.repository import RecipeRepository

from pipeline._cli import get_phase_logger
from pipeline.checkpoint import StructuredLogger, checkpoint

PHASE = "content_generation"
_REQUIRED = ("title", "body_markdown", "ig_caption")


class DraftProducer(Protocol):
    """Produces a draft-content payload for a recipe row."""

    def produce(self, row: RecipeRow) -> dict[str, str]: ...


@dataclass(frozen=True)
class ContentGenReport:
    """Outcome of one content-generation run."""

    eligible: int
    generated: int
    persisted: int


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ContentGenerator:
    """Generates draft content for eligible recipes via an injected producer."""

    def __init__(
        self,
        repo: RecipeRepository,
        producer: DraftProducer,
        *,
        logger: StructuredLogger | None = None,
    ) -> None:
        self._repo = repo
        self._producer = producer
        self._log = logger

    def run(self, *, persist: bool, limit: int | None = None) -> ContentGenReport:
        """Generate content for eligible recipes and run the checkpoint gate."""
        eligible_rows = [
            r
            for r in self._repo.list_recipes()
            if r.dog_safe and r.content_status == ContentStatus.NONE
        ]
        targets = eligible_rows if limit is None else eligible_rows[:limit]
        drafts: list[tuple[RecipeRow, dict[str, str]]] = []
        if persist:
            for row in targets:
                payload = dict(self._producer.produce(row))
                payload.setdefault("generated_at", _now())
                drafts.append((row, payload))
        report = ContentGenReport(
            eligible=len(eligible_rows),
            generated=len(drafts),
            persisted=len(drafts) if persist else 0,
        )
        self._gate(drafts, report)
        if persist:
            for row, payload in drafts:
                self._repo.set_generated_content(
                    row.id, payload, ContentStatus.GENERATED
                )
        return report

    def _gate(
        self,
        drafts: list[tuple[RecipeRow, dict[str, str]]],
        report: ContentGenReport,
    ) -> None:
        """Invariant: every generated draft carries the required content fields."""
        ok = all(
            all(payload.get(field) for field in _REQUIRED)
            for _, payload in drafts
        )
        checkpoint(
            PHASE,
            ok=ok,
            reason="" if ok else "a generated draft is missing required fields",
            logger=self._log,
            eligible=report.eligible,
            generated=report.generated,
            persisted=report.persisted,
        )


class SeedDraftProducer:
    """Production producer: wraps the seed-grounded recipe generator."""

    def produce(self, row: RecipeRow) -> dict[str, str]:
        from generators.recipe import generate_recipe

        try:
            recipe = generate_recipe(row.name, seed_id=row.id)
        except Exception:
            recipe = generate_recipe(row.name)
        return {
            "title": recipe.title,
            "body_markdown": recipe.body_markdown,
            "ig_caption": recipe.ig_caption,
            "image_brief": recipe.image_brief,
        }


def _drafter_probe():  # type: ignore[no-untyped-def]
    from lib.runtime.health_check import HealthCheckResult

    try:
        from generators.drafter import get_drafter

        get_drafter()
        return HealthCheckResult("voice_drafter", True, "drafter provider OK")
    except Exception as exc:
        return HealthCheckResult(
            "voice_drafter", False, f"drafter error: {type(exc).__name__}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Content generation phase (recipe pipeline phase 3)."
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report eligible recipes without generating or persisting",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="verify the voice drafter is configured, then exit",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    log = get_phase_logger(PHASE, args.log_level)

    if args.health_check:
        from lib.runtime.health_check import register, run_health_checks

        register("voice_drafter", _drafter_probe)
        return 0 if run_health_checks(["voice_drafter"]) else 1

    conn = db.connect()
    try:
        db.migrate(conn)
        generator = ContentGenerator(
            RecipeRepository(conn), SeedDraftProducer(), logger=log
        )
        report = generator.run(persist=not args.dry_run, limit=args.limit)
    finally:
        conn.close()

    log.info(
        "content_generation_done",
        eligible=report.eligible,
        generated=report.generated,
        persisted=report.persisted,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
