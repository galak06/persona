# pyright: reportMissingImports=false
"""Phase 2 of the recipe-publisher pipeline: affiliate matching.

Matches each recipe to up to N Amazon-Associates products from the product
catalog (``data/recipe_products.json``), keyed off the recipe's name +
ingredients, and persists the matches (``{key, asin, display}``) to
``recipes.affiliate_products``.

Reuses ``lib.recipe_products`` (catalog loader + matcher) for the matching
logic and the shared lib for structured logging / ``--health-check``. The
affiliate network is Amazon Associates.

Run::

    python -m pipeline.affiliate_matching [--limit 3] [--catalog PATH]
                                          [--dry-run] [--health-check]
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
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

from lib.recipe_products.catalog import RecipeCatalog, load_catalog
from lib.recipe_products.matcher import DEFAULT_LIMIT, pick_products
from pipeline.checkpoint import StructuredLogger, checkpoint

PHASE = "affiliate_matching"


@dataclass(frozen=True)
class AffiliateMatchReport:
    """Outcome of one affiliate-matching run."""

    total: int  # recipes considered
    matched: int  # total product entries across all recipes
    recipes_with_products: int
    persisted: int  # recipe rows written
    limit: int


class AffiliateMatcher:
    """Matches catalog products to recipes via name + ingredient keywords.

    Stateless apart from the injected repository, catalog, and logger, so it is
    trivial to unit-test with an in-memory DB and a hand-built catalog.
    """

    def __init__(
        self,
        repo: RecipeRepository,
        catalog: RecipeCatalog,
        *,
        limit: int = DEFAULT_LIMIT,
        logger: StructuredLogger | None = None,
    ) -> None:
        self._repo = repo
        self._catalog = catalog
        self._limit = limit
        self._log = logger

    def run(self, *, persist: bool) -> AffiliateMatchReport:
        """Match products for every recipe and run the checkpoint gate."""
        rows = self._repo.list_recipes()
        matched = 0
        recipes_with = 0
        persisted = 0
        max_count = 0
        for row in rows:
            # Match against the recipe name *and* its ingredient items so the
            # catalog's keyword type-map can key off ingredients, not just title.
            match_text = " ".join([row.name, *(i.item for i in row.ingredients)])
            products = pick_products(
                row.id, match_text, self._catalog, self._limit
            )
            entries = [
                {"key": p.key, "asin": p.asin, "display": p.display}
                for p in products
            ]
            matched += len(entries)
            max_count = max(max_count, len(entries))
            if entries:
                recipes_with += 1
            if persist:
                # Write even empty lists so re-runs clear stale matches.
                self._repo.set_affiliate_products(row.id, entries)
                persisted += 1
        report = AffiliateMatchReport(
            total=len(rows),
            matched=matched,
            recipes_with_products=recipes_with,
            persisted=persisted,
            limit=self._limit,
        )
        self._gate(report, max_count)
        return report

    def _gate(self, report: AffiliateMatchReport, max_count: int) -> None:
        """End-of-phase invariant: a positive limit, never exceeded per recipe."""
        ok = report.limit > 0 and max_count <= report.limit
        checkpoint(
            PHASE,
            ok=ok,
            reason="" if ok else "a recipe exceeded the product limit",
            logger=self._log,
            total=report.total,
            matched=report.matched,
            recipes_with_products=report.recipes_with_products,
            persisted=report.persisted,
            limit=report.limit,
        )


def _catalog_probe(catalog_path: Path | None):  # type: ignore[no-untyped-def]
    """Health probe: the product catalog loads and validates."""
    from lib.runtime.health_check import HealthCheckResult

    try:
        catalog = load_catalog(catalog_path)
        detail = f"catalog OK ({len(catalog.products)} products)"
        if not os.environ.get("AMAZON_ASSOCIATES_TAG"):
            detail += "; warning: AMAZON_ASSOCIATES_TAG unset (links unmonetized)"
        return HealthCheckResult("affiliate_catalog", True, detail)
    except Exception as exc:
        return HealthCheckResult(
            "affiliate_catalog", False, f"catalog error: {type(exc).__name__}"
        )


def _run_health_check(catalog_path: Path | None) -> int:
    from lib.runtime.health_check import register, run_health_checks

    register("affiliate_catalog", lambda: _catalog_probe(catalog_path))
    return 0 if run_health_checks(["affiliate_catalog"]) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Affiliate matching phase (recipe pipeline phase 2)."
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help="max products per recipe"
    )
    parser.add_argument("--catalog", help="path to recipe_products.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute matches without persisting them",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="verify the product catalog loads, then exit",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    catalog_path = Path(args.catalog) if args.catalog else None

    from lib.observability.logger import configure_logging, get_logger

    configure_logging(level=args.log_level)
    log = get_logger(PHASE)

    if args.health_check:
        return _run_health_check(catalog_path)

    catalog = load_catalog(catalog_path)
    conn = db.connect()
    try:
        db.migrate(conn)
        matcher = AffiliateMatcher(
            RecipeRepository(conn), catalog, limit=args.limit, logger=log
        )
        report = matcher.run(persist=not args.dry_run)
    finally:
        conn.close()

    log.info(
        "affiliate_matching_done",
        total=report.total,
        matched=report.matched,
        recipes_with_products=report.recipes_with_products,
        persisted=report.persisted,
        limit=report.limit,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
