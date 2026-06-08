"""Command-line entrypoint for the recipe DB pipeline.

    python -m recipe_db.cli add <url> [--html FILE] [--dry-run]
    python -m recipe_db.cli list [--status STATUS]
    python -m recipe_db.cli export <id> [--override] [--dry-run]
    python -m recipe_db.cli --health-check

Mutating runs (``add`` / ``export`` without ``--dry-run``) are guarded by an
``fcntl`` file lock so concurrent cron invocations never collide. No secrets are
read here — the DB path is resolved from ``BRAND_DIR`` via ``recipe_db.db``.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import logging
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from recipe_db import (
    batch,
    db,
    normalize,
    rename,
    safety,
    scraper,
    seed_exporter,
)
from recipe_db.models import RecipeRow, RecipeStatus, slugify
from recipe_db.repository import RecipeRepository

logger = logging.getLogger("recipe_db.cli")


@contextlib.contextmanager
def _singleton_lock(db_path: Path) -> Iterator[bool]:
    """Yield True if the exclusive lock was acquired, False if already held."""
    lock_path = db_path.parent / f"{db_path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _emit(message: str) -> None:
    """Write a user-facing line to stdout (CLI output, not a log record)."""
    sys.stdout.write(f"{message}\n")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _build_row(
    raw: dict[str, object], url: str
) -> tuple[RecipeRow, list[str], bool]:
    """Scrape-result -> RecipeRow, progressing status and scanning safety."""
    scraped = normalize.normalize(raw, url)
    flags, dog_safe = safety.scan_ingredients(scraped.ingredients)
    row = RecipeRow(
        name=scraped.name,
        ingredients=scraped.ingredients,
        steps=scraped.steps,
        prep_minutes=scraped.prep_minutes,
        cook_minutes=scraped.cook_minutes,
        total_minutes=scraped.total_minutes,
        servings=scraped.servings,
        nutrition=scraped.nutrition,
        category=scraped.category,
        tags=scraped.tags,
        hero_image_url=scraped.hero_image_url,
        source_url=scraped.source_url,
        source_name=scraped.source_name,
        license=scraped.license,
        content_hash=scraped.content_hash,
        id=slugify(scraped.name),
        status=RecipeStatus.SAFETY_CHECKED,
        toxic_flags=flags,
        dog_safe=dog_safe,
    )
    return row, flags, dog_safe


def _cmd_add(args: argparse.Namespace) -> int:
    html = Path(args.html).read_text(encoding="utf-8") if args.html else None
    raw = scraper.scrape(args.url, html=html)
    if raw is None:
        logger.error("no schema.org Recipe found at %s", args.url)
        return 2

    row, flags, dog_safe = _build_row(raw, args.url)
    if not row.name:
        logger.error("scraped recipe has no name; refusing to store")
        return 2

    verdict = "DOG-SAFE" if dog_safe else f"NOT SAFE ({', '.join(flags)})"
    if args.dry_run:
        _emit(f"[dry-run] {row.id} :: {row.name}")
        _emit(f"  ingredients: {len(row.ingredients)}  steps: {len(row.steps)}")
        _emit(
            f"  prep={row.prep_minutes}m cook={row.cook_minutes}m "
            f"servings={row.servings or '-'}"
        )
        _emit(f"  safety: {verdict}")
        return 0

    row.display_name = rename.generate_display_name(
        row.name, [ing.item for ing in row.ingredients]
    )
    conn = db.connect()
    try:
        db.migrate(conn)
        repo = RecipeRepository(conn)
        repo.insert_raw(
            source_url=row.source_url,
            source_name=row.source_name,
            payload=raw,
            content_hash=row.content_hash,
            scraped_at=_now_iso(),
        )
        repo.upsert_recipe(row)
    finally:
        conn.close()
    shown = row.display_name or row.name
    _emit(f"stored {row.id} :: {shown}  [{verdict}]")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        db.migrate(conn)
        rows = RecipeRepository(conn).list_recipes(status=args.status)
    finally:
        conn.close()
    if not rows:
        _emit("(no recipes)")
        return 0
    for row in rows:
        safe = "safe" if row.dog_safe else "UNSAFE"
        _emit(f"{row.id}\t{row.status}\t{safe}\t{row.display_name or row.name}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        db.migrate(conn)
        repo = RecipeRepository(conn)
        row = repo.get_recipe(args.id)
        if row is None:
            logger.error("no recipe with id '%s'", args.id)
            return 2
        if args.override:
            row.override = True
        try:
            seed = seed_exporter.export_seed(row, dry_run=args.dry_run)
        except ValueError as exc:
            logger.error("%s", exc)
            return 3
        if args.dry_run:
            _emit(json.dumps(seed, ensure_ascii=False, indent=2))
            return 0
        repo.set_status(row.id, RecipeStatus.SEED_EXPORTED)
    finally:
        conn.close()
    _emit(f"exported seed '{seed['id']}' and marked status=seed_exported")
    return 0


def _cmd_scrape_category(args: argparse.Namespace) -> int:
    summary = batch.scrape_category(
        args.url,
        limit=args.limit,
        dry_run=args.dry_run,
        do_export=args.export,
        now_iso=_now_iso(),
        namer=rename.generate_display_name,
    )
    _emit(
        f"found {summary.found_links} recipe links; "
        f"stored={summary.count(batch.STORED) + summary.count(batch.EXPORTED)} "
        f"exported={summary.count(batch.EXPORTED)} "
        f"no_recipe={summary.count(batch.NO_RECIPE)} "
        f"errors={summary.count(batch.ERROR)}"
    )
    for outcome in summary.outcomes:
        safe = "safe" if outcome.dog_safe else "UNSAFE"
        _emit(
            f"  [{outcome.status}] {safe}  {outcome.name or outcome.url}"
        )
    return 0


def _run_locked(args: argparse.Namespace) -> int:
    """Run a mutating command under the singleton lock."""
    db_path = db.resolve_db_path()
    with _singleton_lock(db_path) as acquired:
        if not acquired:
            _emit("another recipe_db run holds the lock; exiting cleanly")
            return 0
        exit_code: int = args.func(args)
        return exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recipe_db.cli", description="Recipe scrape/normalize/export CLI."
    )
    parser.add_argument(
        "--health-check", action="store_true",
        help="open + migrate the DB, print 'ok', exit 0 (non-zero on failure)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
    )
    sub = parser.add_subparsers(dest="command")

    p_add = sub.add_parser("add", help="scrape+normalize+safety-check a URL")
    p_add.add_argument("url")
    p_add.add_argument("--html", help="read local HTML file (offline)")
    p_add.add_argument("--dry-run", action="store_true")
    p_add.set_defaults(func=_cmd_add, mutating=True)

    p_list = sub.add_parser("list", help="list stored recipes")
    p_list.add_argument("--status", choices=sorted(RecipeStatus.ALL))
    p_list.set_defaults(func=_cmd_list, mutating=False)

    p_export = sub.add_parser("export", help="export a recipe into seeds.json")
    p_export.add_argument("id")
    p_export.add_argument("--override", action="store_true")
    p_export.add_argument("--dry-run", action="store_true")
    p_export.set_defaults(func=_cmd_export, mutating=True)

    p_cat = sub.add_parser(
        "scrape-category",
        help="crawl a listing page, store recipes with enough ratings",
    )
    p_cat.add_argument("url")
    p_cat.add_argument(
        "--limit", type=int, default=None,
        help="cap how many recipe links to process",
    )
    p_cat.add_argument(
        "--export", action="store_true",
        help="also export dog-safe recipes into seeds.json",
    )
    p_cat.add_argument("--dry-run", action="store_true")
    p_cat.set_defaults(func=_cmd_scrape_category, mutating=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.health_check:
        try:
            conn = db.connect()
            db.migrate(conn)
            conn.close()
        except Exception as exc:  # report any failure as a non-zero exit
            logger.error("health-check failed: %s", exc)
            return 1
        _emit("ok")
        return 0

    if not getattr(args, "command", None):
        parser.print_help()
        return 1

    mutating = getattr(args, "mutating", False)
    if mutating and not getattr(args, "dry_run", False):
        return _run_locked(args)
    exit_code: int = args.func(args)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
