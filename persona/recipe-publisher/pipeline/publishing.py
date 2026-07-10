# pyright: reportMissingImports=false
"""Phase 8 of the recipe-publisher pipeline: multi-platform publishing.

Publishes approved, de-duplicated, rate-allowed recipes to the configured
platforms (Instagram, Facebook, Pinterest). Composes the phase-6 dedup gate,
the phase-7 rate-limit gate, and the phase-9 retry loop, and records every
attempt to ``recipes.publish_results`` (the local outcome log read by phase 10).

DRAFT-BEFORE-PUBLISH: ``dry_run=True`` is the default. A dry run records each
intended publish as ``{status: "dry_run"}`` WITHOUT calling any platform and
without advancing ``content_status``. Real publishing calls the injected
``PlatformPublisher`` with retry; the live production adapter (assembling the
Recipe + carousel/image assets each platform needs) is intentionally NOT wired
here — ``--no-dry-run`` requires an explicit injected publisher.

Run::

    python -m pipeline.publishing [--platforms ig,fb] [--health-check]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol

from recipe_db import db
from recipe_db.models import ContentStatus, RecipeRow
from recipe_db.repository import RecipeRepository

from pipeline._cli import get_phase_logger
from pipeline.checkpoint import StructuredLogger, checkpoint
from pipeline.dedup_check import DedupChecker
from pipeline.rate_limiting import DEFAULT_DAILY_CAPS, RateLimitGate
from pipeline.retry import RetryExhaustedError, retry_call

PHASE = "publishing"
PLATFORMS: tuple[str, ...] = ("ig", "fb", "pinterest")


class PlatformPublisher(Protocol):
    """Publishes one recipe to one platform; returns ``{ref, url}`` or raises."""

    def publish(self, platform: str, row: RecipeRow) -> dict[str, str]: ...


@dataclass(frozen=True)
class PublishReport:
    """Outcome of one publishing run."""

    approved: int
    duplicates: int
    published: int
    failed: int
    skipped_rate_limited: int
    dry_run: bool


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _is_transient(exc: BaseException) -> bool:
    """Treat value/type/key errors as permanent; everything else transient."""
    return not isinstance(exc, (ValueError, TypeError, KeyError))


class PublishOrchestrator:
    """Publishes approved recipes across platforms behind dedup + rate gates."""

    def __init__(
        self,
        repo: RecipeRepository,
        *,
        publisher: PlatformPublisher | None = None,
        rate_gate: RateLimitGate | None = None,
        platforms: tuple[str, ...] = PLATFORMS,
        dry_run: bool = True,
        attempts: int = 3,
        logger: StructuredLogger | None = None,
    ) -> None:
        self._repo = repo
        self._publisher = publisher
        self._rate = rate_gate or RateLimitGate()
        self._platforms = platforms
        self._dry_run = dry_run
        self._attempts = attempts
        self._log = logger

    def run(self, *, today: str | None = None) -> PublishReport:
        day = today or date.today().isoformat()
        # Phase 6: drop already-published duplicates from the queue first.
        dedup = DedupChecker(self._repo, logger=self._log).run(persist=True)
        history = self._today_history(day)
        published = failed = rate_skipped = 0
        approved_rows = self._repo.list_by_content_status(ContentStatus.APPROVED)
        for row in approved_rows:
            results = list(row.publish_results)
            any_published = False
            for platform in self._platforms:
                # Phase 7: per-platform daily cap.
                if not self._rate.allow(platform, day, history):
                    rate_skipped += 1
                    results.append(
                        {"platform": platform, "status": "skipped_rate_limited", "at": _now()}
                    )
                    continue
                if self._dry_run:
                    results.append(
                        {"platform": platform, "status": "dry_run", "at": _now(), "attempts": "0"}
                    )
                    continue
                outcome = self._publish_one(platform, row)
                results.append(outcome)
                if outcome["status"] == "published":
                    history.append((platform, day))
                    published += 1
                    any_published = True
                else:
                    failed += 1
            self._repo.set_publish_results(row.id, results)
            if any_published:
                self._repo.set_content_status(row.id, ContentStatus.PUBLISHED)
        report = PublishReport(
            approved=len(approved_rows),
            duplicates=dedup.duplicates,
            published=published,
            failed=failed,
            skipped_rate_limited=rate_skipped,
            dry_run=self._dry_run,
        )
        self._gate(report, day, history)
        return report

    def _publish_one(self, platform: str, row: RecipeRow) -> dict[str, str]:
        # Phase 9: retry transient failures.
        if self._publisher is None:
            raise RuntimeError(
                "live publishing requires an injected PlatformPublisher; "
                "use dry_run=True or wire a production publisher"
            )
        publisher = self._publisher
        try:
            out, attempts = retry_call(
                lambda: publisher.publish(platform, row),
                attempts=self._attempts,
                is_transient=_is_transient,
                logger=self._log,
            )
            return {
                "platform": platform,
                "status": "published",
                "ref": out.get("ref", ""),
                "url": out.get("url", ""),
                "at": _now(),
                "attempts": str(attempts),
            }
        except RetryExhaustedError as exc:
            return {
                "platform": platform,
                "status": "failed",
                "error": type(exc.last).__name__,
                "at": _now(),
                "attempts": str(exc.attempts),
            }
        except Exception as exc:  # permanent (non-transient) failure
            return {
                "platform": platform,
                "status": "failed",
                "error": type(exc).__name__,
                "at": _now(),
                "attempts": "1",
            }

    def _today_history(self, day: str) -> list[tuple[str, str]]:
        history: list[tuple[str, str]] = []
        for row in self._repo.list_recipes():
            for res in row.publish_results:
                if res.get("status") == "published" and res.get("at", "")[:10] == day:
                    history.append((res.get("platform", ""), day))
        return history

    def _gate(
        self, report: PublishReport, day: str, history: list[tuple[str, str]]
    ) -> None:
        """Invariant: no platform exceeded its daily cap this run."""
        over = [
            platform
            for platform in self._platforms
            if self._rate.used(platform, day, history) > self._rate.cap(platform)
        ]
        checkpoint(
            PHASE,
            ok=not over,
            reason="" if not over else f"daily cap exceeded for {over}",
            logger=self._log,
            approved=report.approved,
            duplicates=report.duplicates,
            published=report.published,
            failed=report.failed,
            skipped_rate_limited=report.skipped_rate_limited,
            dry_run=report.dry_run,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Multi-platform publishing phase (recipe pipeline phase 8)."
    )
    parser.add_argument(
        "--platforms",
        default=",".join(PLATFORMS),
        help="comma-separated platforms (ig,fb,pinterest)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="actually publish (requires a wired production publisher)",
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

    platforms = tuple(p.strip() for p in args.platforms.split(",") if p.strip())
    caps = {p: DEFAULT_DAILY_CAPS.get(p, 0) for p in platforms}
    conn = db.connect()
    try:
        db.migrate(conn)
        orchestrator = PublishOrchestrator(
            RecipeRepository(conn),
            rate_gate=RateLimitGate(caps),
            platforms=platforms,
            dry_run=not args.no_dry_run,
            logger=log,
        )
        report = orchestrator.run()
    finally:
        conn.close()

    log.info(
        "publishing_done",
        approved=report.approved,
        duplicates=report.duplicates,
        published=report.published,
        failed=report.failed,
        skipped_rate_limited=report.skipped_rate_limited,
        dry_run=report.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
