#!/usr/bin/env python3
"""Seed `comment_claims` with every post we have already commented on.

`lib/comment_outbox.py` makes `comment_claims` the permanent "never comment
here twice" memory -- but an empty table only starts remembering the day it
ships. The store it replaces, `lib/deduplication.py`'s
`<BRAND_DIR>/state/dedup_cache.json`, forgets on a 60-day TTL and holds ONE
entry per post, so a later `like` mark from `scripts/ig_like.py` silently
overwrites an earlier `comment` mark: posts we have already commented on are
*already* invisible to `already_commented()`. Carrying that history forward is
what upgrades the memory from "60-day, clobberable JSON" to "permanent
Postgres".

Three sources, unioned richest-first on `(platform, post_id)`:

1. `logs/engagement_log.jsonl` where `action == "comment"` -- the only
   append-only record carrying post_id AND post_url AND content AND
   target_name, which is exactly what settling a stale claim needs.
2. Postgres `completed_tasks` where `task_type = 'comment'` -- written by the
   legacy drainer `lib/engagement/commenter.py` (used by `scripts/ig_comment.py`)
   through `lib/dedup_pg.py`.
3. The JSON dedup cache -- entries with `action == "comment"` and
   `status == "engaged"`, i.e. precisely what `already_commented()` reads today.

`engagements` (`lib/engagements_db`) is deliberately NOT a source and must not
be added as one later: `record_publish` slugifies the post id into the primary
key (`dedup_id(platform, kind, ref)`, `lib/engagements_db/models.py:52-58`)
and `lib/engagement/inline_comment.py` passes `source_ref` empty, so the post
id cannot be read back out of a row. Those rows are lossy by construction.

Every row lands as `status = 'posted'`, never `pending`: this is settled
history, and a `pending` row would be handed to the verify action, which would
reopen a months-old post hunting for a comment to confirm. `ON CONFLICT DO
NOTHING` makes the copy additive by construction -- re-runnable, and unable to
stomp a live `pending` claim a running engager owns.

The table itself reaches production only through the `docker-compose.yml:64`
initdb mount (empty data dir only; there is no migration runner), so apply
`db/schema.sql` on a live stack first -- see `_SCHEMA_HINT` below.

Usage::

    python scripts/backfill_comment_claims.py --dry-run
    python scripts/backfill_comment_claims.py
    python scripts/backfill_comment_claims.py --brand acme-dogs
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.brand_context import BrandContext, current_brand_id
from lib.db import fetch_all, get_connection

# `comment_claims` is the FB/IG engagers' outbox (`lib.comment_outbox.Platform`).
# WordPress replies run through their own human-gated flow and would only be
# dead weight here.
PLATFORMS = frozenset({"facebook", "instagram"})

_SCHEMA_HINT = (
    "comment_claims does not exist. Apply db/schema.sql first -- it only runs "
    "automatically on an empty data dir (docker-compose.yml:64 initdb mount), "
    "so on a live stack run:\n"
    "  docker compose exec -T postgres psql -U persona -d persona < app/db/schema.sql"
)

_INSERT_SQL = """
INSERT INTO comment_claims
    (brand, platform, post_id, status, post_url, target_name, content,
     worker_label, claimed_at, settled_at)
VALUES (%(brand)s, %(platform)s, %(post_id)s, 'posted', %(post_url)s,
        %(target_name)s, %(content)s, 'backfill', %(at)s, %(at)s)
ON CONFLICT (brand, platform, post_id) DO NOTHING
"""


class TableMissingError(RuntimeError):
    """Raised when `comment_claims` has not been created yet."""


def _require_table() -> None:
    """Fail loudly -- never silently no-op -- when the table is absent."""
    try:
        fetch_all("SELECT 1 FROM comment_claims LIMIT 1")
    except psycopg.errors.UndefinedTable as exc:
        raise TableMissingError(_SCHEMA_HINT) from exc


def _parse_at(raw: str) -> datetime:
    """Best-effort timestamp for a history row; now() when unreadable."""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def from_engagement_log(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    """Comment rows from the append-only JSONL log -- the richest source.

    Keeps the FIRST entry per post: when the duplicate-comment bug fired, the
    earliest line is the comment that was actually meant to happen.
    """
    rows: dict[tuple[str, str], dict[str, str]] = {}
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # half-written line -- the history parser skips these too
            if not isinstance(entry, dict) or entry.get("action") != "comment":
                continue
            platform = str(entry.get("platform") or "")
            post_id = str(entry.get("post_id") or "")
            if platform not in PLATFORMS or not post_id:
                continue  # pre-2026-08 lines carry no post_id -- unclaimable
            rows.setdefault(
                (platform, post_id),
                {
                    "post_url": str(entry.get("post_url") or ""),
                    "target_name": str(entry.get("target_name") or ""),
                    "content": str(entry.get("content") or ""),
                    "at": str(entry.get("timestamp") or entry.get("date") or ""),
                },
            )
    return rows


def from_completed_tasks(brand: str) -> dict[tuple[str, str], dict[str, str]]:
    """Comment rows the legacy drainer recorded in Postgres."""
    found = fetch_all(
        """
        SELECT platform, entity_id, completed_at
        FROM completed_tasks
        WHERE task_type = 'comment' AND brand = %s
        """,
        (brand,),
    )
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for row in found:
        platform = str(row.get("platform") or "")
        post_id = str(row.get("entity_id") or "")
        if platform not in PLATFORMS or not post_id:
            continue
        rows[(platform, post_id)] = {
            "post_url": "",
            "target_name": "",
            "content": "",
            "at": str(row.get("completed_at") or ""),
        }
    return rows


def from_dedup_cache() -> dict[tuple[str, str], dict[str, str]]:
    """Comment marks still visible to `deduplication.already_commented`.

    Read-only on purpose: `lib.deduplication._load_cache` rewrites a corrupt
    file, and a backfill has no business editing the store it is preserving.
    """
    path = BrandContext.from_env().paths.dedup_cache
    rows: dict[tuple[str, str], dict[str, str]] = {}
    if not path.exists():
        return rows
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return rows
    if not isinstance(cache, dict):
        return rows
    for platform, posts in cache.items():
        if platform not in PLATFORMS or not isinstance(posts, dict):
            continue
        for post_id, entry in posts.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("action") != "comment" or entry.get("status") != "engaged":
                continue
            rows[(platform, str(post_id))] = {
                "post_url": "",
                "target_name": str(entry.get("group_or_hashtag") or ""),
                "content": "",
                "at": str(entry.get("timestamp") or entry.get("engaged_at") or ""),
            }
    return rows


def _claim_count(brand: str) -> int:
    rows = fetch_all("SELECT COUNT(*) AS n FROM comment_claims WHERE brand = %s", (brand,))
    return int(rows[0]["n"]) if rows else 0


def backfill(brand: str, *, dry_run: bool = False) -> dict[str, int]:
    """Union the three history sources into `comment_claims`. Additive only."""
    if brand == "default":
        raise ValueError("refusing to backfill the 'default' brand")
    _require_table()

    log_rows = from_engagement_log(BrandContext.from_env().paths.logs_dir / "engagement_log.jsonl")
    task_rows = from_completed_tasks(brand)
    cache_rows = from_dedup_cache()
    # Richest last: the log's post_url/content/target_name win the merge.
    merged = {**cache_rows, **task_rows, **log_rows}

    before = _claim_count(brand)
    print(f"engagement_log.jsonl : {len(log_rows)} commented posts")
    print(f"completed_tasks      : {len(task_rows)} commented posts")
    print(f"dedup_cache.json     : {len(cache_rows)} commented posts")
    print(f"union                : {len(merged)} unique (platform, post_id)")
    print(f"comment_claims['{brand}'] : {before} rows (before)")

    stats = {
        "engagement_log": len(log_rows),
        "completed_tasks": len(task_rows),
        "dedup_cache": len(cache_rows),
        "unique": len(merged),
        "before": before,
        "inserted": 0,
        "after": before,
    }
    if dry_run:
        print(f"DRY RUN -- would insert up to {len(merged)} 'posted' rows, writing nothing")
        return stats

    with get_connection() as conn, conn.cursor() as cur:
        for (platform, post_id), row in merged.items():
            cur.execute(
                _INSERT_SQL,
                {
                    "brand": brand,
                    "platform": platform,
                    "post_id": post_id,
                    "post_url": row["post_url"],
                    "target_name": row["target_name"][:255],
                    "content": row["content"],
                    "at": _parse_at(row["at"]),
                },
            )

    after = _claim_count(brand)
    stats["after"] = after
    stats["inserted"] = after - before
    print(f"comment_claims['{brand}'] : {after} rows (after) -- {after - before} inserted")
    print("existing rows untouched -- ON CONFLICT DO NOTHING, never UPDATE")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed comment_claims from comment history.")
    parser.add_argument(
        "--brand",
        default=None,
        help="brand id to seed (default: resolved from PERSONA_BRAND / BRAND_DIR)",
    )
    parser.add_argument("--dry-run", action="store_true", help="report counts, write nothing")
    args = parser.parse_args()

    brand = args.brand or current_brand_id()
    if brand == "default":
        print(
            "refusing to backfill the 'default' brand -- set PERSONA_BRAND "
            "or BRAND_DIR, or pass --brand explicitly",
            file=sys.stderr,
        )
        return 2

    try:
        backfill(brand, dry_run=args.dry_run)
    except TableMissingError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"backfill failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
