#!/usr/bin/env python3
"""Merge the legacy engine-level dedup state into the brand's own state dir.

`lib/deduplication.py` and `lib/draft_history.py` wrote to engine-relative
paths under `app/.claude/state/` until 2026-08-15. That was wrong three ways:
the path is not mounted into the worker container (so the file was ephemeral
and every rebuild reset the like/comment history), `scripts/status.py` read
the brand-scoped path instead, and one file was shared by every brand.

Both modules now resolve `<BRAND_DIR>/state/...`. This script merges whatever
the old engine-level files still hold into the new location so the switch does
not throw away live history.

Additive by construction, per the repo's additive-only rule:

* The legacy files are READ, never modified or deleted.
* Existing brand-side entries WIN on conflict unless the legacy entry is
  strictly newer (`engaged_at`), so a re-run cannot regress fresher state.
* Entries already past their TTL are skipped — `deduplication` would purge
  them on the next load anyway, so importing them is pure noise.
* Re-running is a no-op once merged.

Usage::

    python scripts/migrate_dedup_cache.py --dry-run
    python scripts/migrate_dedup_cache.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.deduplication import TTL_DAYS
from lib.io.jsonio import write_json

LEGACY_DEDUP = PROJECT_ROOT / ".claude" / "state" / "dedup_cache.json"
LEGACY_DRAFTS = PROJECT_ROOT / ".claude" / "state" / "recent_drafts.jsonl"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def merge_dedup(legacy: Path, target: Path, *, dry_run: bool) -> tuple[int, int]:
    """Merge legacy dedup entries into `target`. Returns (added, skipped_stale)."""
    old = _read_json(legacy)
    new = _read_json(target)
    cutoff = (date.today() - timedelta(days=TTL_DAYS)).isoformat()

    added = stale = 0
    for platform, posts in old.items():
        if not isinstance(posts, dict):
            continue
        bucket = new.setdefault(platform, {})
        for post_id, entry in posts.items():
            when = str(entry.get("engaged_at", ""))
            if when < cutoff:
                stale += 1
                continue
            existing = bucket.get(post_id)
            # Brand-side state wins unless the legacy entry is strictly newer.
            if existing is not None and str(existing.get("engaged_at", "")) >= when:
                continue
            bucket[post_id] = entry
            added += 1

    if added and not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, new)
    return added, stale


def merge_drafts(legacy: Path, target: Path, *, dry_run: bool) -> int:
    """Append legacy draft-history lines missing from `target`. Returns added."""
    if not legacy.exists():
        return 0
    old_lines = [ln for ln in legacy.read_text(encoding="utf-8").splitlines() if ln.strip()]
    existing = set()
    if target.exists():
        existing = {ln for ln in target.read_text(encoding="utf-8").splitlines() if ln.strip()}

    missing = [ln for ln in old_lines if ln not in existing]
    if missing and not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            for line in missing:
                f.write(line + "\n")
    return len(missing)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args()

    from lib.config import settings

    paths = settings.paths
    if paths is None:
        print("BRAND_DIR did not resolve; set it and retry", file=sys.stderr)
        return 2

    print(f"brand state dir: {paths.state_dir}")

    added, stale = merge_dedup(LEGACY_DEDUP, paths.dedup_cache, dry_run=args.dry_run)
    print(f"dedup_cache.json   : +{added} entries ({stale} skipped, past {TTL_DAYS}d TTL)")

    drafts = merge_drafts(LEGACY_DRAFTS, paths.recent_drafts, dry_run=args.dry_run)
    print(f"recent_drafts.jsonl: +{drafts} lines")

    if args.dry_run:
        print("DRY RUN — nothing written")
    else:
        print(f"legacy files left untouched at {LEGACY_DEDUP.parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
