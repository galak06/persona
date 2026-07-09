# pyright: reportMissingImports=false
"""Demo runner: walk a copy of the brand recipe DB through all 10 pipeline phases.

Safe by design: operates on a SANDBOX COPY of ``BRAND_DIR/data/recipes.db`` — the
real brand DB is never modified. Content is produced from each recipe's own DB
fields (a stub producer, NO LLM call) and publishing uses a demo publisher (NO
network), so the whole pipeline runs fully offline with no API keys.

Usage::

    BRAND_DIR=/…/persona python scripts/run_pipeline_demo.py \
        [--limit 5] [--dry-run] [--keep-db]

``--dry-run`` makes the publishing phase record intent instead of marking
recipes published; ``--keep-db`` retains the sandbox DB and prints its path.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

# Bridge to the recipe-publisher root (recipe_db/pipeline) and social-automation
# root (lib.*), regardless of CWD.
_RP = Path(__file__).resolve().parent.parent
_SA = _RP.parent
for _root in (_RP, _SA):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from pipeline import seasons
from pipeline._cli import get_phase_logger
from pipeline.affiliate_matching import AffiliateMatcher
from pipeline.analytics import AnalyticsTracker
from pipeline.approval import ApprovalService
from pipeline.content_generation import ContentGenerator
from pipeline.dedup_check import DedupChecker
from pipeline.pending_review import ReviewStager
from pipeline.publishing import PublishOrchestrator
from pipeline.rate_limiting import RateLimitGate
from pipeline.seasonal_selection import SeasonalSelector
from recipe_db import db
from recipe_db.models import ContentStatus, RecipeRow
from recipe_db.repository import RecipeRepository

from lib.recipe_products.catalog import load_catalog


class _StubProducer:
    """Builds draft content from the recipe's own DB fields — no LLM call."""

    def produce(self, row: RecipeRow) -> dict[str, str]:
        body = "\n".join(f"- {ing.item}" for ing in row.ingredients) or "Mix and bake."
        return {
            "title": row.display_name or row.name,
            "body_markdown": f"## {row.name}\n\n{body}",
            "ig_caption": (
                f"{row.name} — a wholesome homemade dog treat 🐾 "
                "Comment RECIPE for the printable card!"
            ),
            "image_brief": f"Overhead shot of {row.name} made for dogs.",
        }


class _DemoPublisher:
    """Returns fake refs — demonstrates publishing without any network call."""

    def publish(self, platform: str, row: RecipeRow) -> dict[str, str]:
        return {
            "ref": f"demo-{platform}-{row.id}",
            "url": f"https://demo.local/{platform}/{row.id}",
        }


def _line(phase: str, detail: str) -> None:
    print(f"  ✓ phase {phase:<22} {detail}")


def _brand_dir() -> Path:
    brand = os.environ.get("BRAND_DIR")
    if not brand:
        raise SystemExit("BRAND_DIR is required (e.g. /…/persona)")
    return Path(brand)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run all 10 pipeline phases on a sandbox copy of the brand DB."
    )
    parser.add_argument(
        "--limit", type=int, default=5, help="recipes to push through content→publish"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="publishing records intent instead of marking recipes published",
    )
    parser.add_argument(
        "--keep-db", action="store_true", help="keep the sandbox DB and print its path"
    )
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)

    log = get_phase_logger("pipeline_demo", args.log_level)
    brand = _brand_dir()
    src_db = brand / "data" / "db" / "recipes.db"
    catalog_path = brand / "data" / "config" / "recipe_products.json"
    if not src_db.exists():
        raise SystemExit(f"brand recipes.db not found: {src_db}")

    sandbox_dir = Path(tempfile.mkdtemp(prefix="pipeline_demo_"))
    sandbox = sandbox_dir / "recipes.db"
    shutil.copy(src_db, sandbox)
    print(f"▶ sandbox copy: {sandbox}  (real brand DB untouched)")

    conn = db.connect(sandbox)
    try:
        db.migrate(conn)
        repo = RecipeRepository(conn)
        print(f"▶ {len(repo.list_recipes())} recipes copied from brand\n")

        season = seasons.current_season()
        s = SeasonalSelector(repo, logger=log).run(season=season, persist=True)
        _line("1 seasonal_selection", f"season={s.season} selected={s.selected}/{s.total}")

        a = AffiliateMatcher(repo, load_catalog(catalog_path), logger=log).run(
            persist=True
        )
        _line("2 affiliate_matching", f"matched={a.matched} recipes={a.recipes_with_products}")

        c = ContentGenerator(repo, _StubProducer(), logger=log).run(
            persist=True, limit=args.limit
        )
        _line("3 content_generation", f"eligible={c.eligible} generated={c.generated}")

        r = ReviewStager(repo, logger=log).run(persist=True)
        _line("4 pending_review", f"staged={r.staged} incomplete={r.incomplete}")

        pending = [row.id for row in repo.list_by_content_status(ContentStatus.PENDING)]
        approver = ApprovalService(repo, logger=log)
        for rid in pending:
            approver.approve(rid)
        _line("5 approval", f"approved={len(pending)} (auto, demo)")

        d = DedupChecker(repo, logger=log).run(persist=True)
        _line("6 dedup_check", f"unique={d.unique} duplicates={d.duplicates}")

        p = PublishOrchestrator(
            repo,
            publisher=_DemoPublisher(),
            rate_gate=RateLimitGate(),
            dry_run=args.dry_run,
            logger=log,
        ).run(today=date.today().isoformat())
        _line(
            "7-9 publishing",
            f"published={p.published} failed={p.failed} "
            f"rate_skipped={p.skipped_rate_limited} dry_run={p.dry_run}",
        )

        an = AnalyticsTracker(repo, logger=log).run()
        _line("10 analytics", f"attempts={an.attempts} by_status={an.by_status}")

        dist: dict[str, int] = {}
        for row in repo.list_recipes():
            dist[row.content_status] = dist.get(row.content_status, 0) + 1
        print(f"\n▶ content_status distribution: {dist}")
    finally:
        conn.close()
        if args.keep_db:
            print(f"▶ sandbox kept: {sandbox}")
        else:
            shutil.rmtree(sandbox_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
