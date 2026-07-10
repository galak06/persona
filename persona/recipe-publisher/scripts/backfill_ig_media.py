"""Backfill IG caption + reel/post split onto already-migrated recipes.

The publish migration (``migrate_published_recipes``) moved each campaign
folder to ``data/media/_migrated_backup/<id>/`` AFTER importing the recipe row, so
the live ``sync_publish_status`` (which scans ``campaigns/**``) can no longer
see those records. This one-off reads the backup ``metadata.json`` for every
recipe already in ``recipes.db`` and MERGES the IG fields the UI popup needs
(``caption`` / ``reel_url`` / ``post_url``) into ``publish_status['ig']`` —
without disturbing the wp / pdf / fb channels.

Idempotent: re-running only rewrites the ig sub-object. ``post_url`` stays
empty until a real single-image IG post step exists. BRAND_DIR comes from the
environment (loaded via lib.local_env) — never inlined.

    python -m scripts.backfill_ig_media            # dry-run
    python -m scripts.backfill_ig_media --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from recipe_db import db
from recipe_db.repository import RecipeRepository

logger = logging.getLogger("backfill_ig_media")


def _load_backup_meta(backup_root: Path, recipe_id: str) -> dict[str, Any] | None:
    """Return the migrated-backup metadata.json for a recipe id, if present."""
    for candidate in (
        backup_root / recipe_id / "metadata.json",
        backup_root / "not_in_wp" / recipe_id / "metadata.json",
    ):
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return data if isinstance(data, dict) else None
    return None


def _ig_fields(meta: dict[str, Any]) -> dict[str, str]:
    """Extract the popup-facing IG fields from a campaign metadata record."""
    reel = str(meta.get("ig_reel_permalink") or "")
    return {
        "caption": str(meta.get("ig_caption") or ""),
        "reel_url": reel,
        "post_url": str(meta.get("ig_post_permalink") or meta.get("ig_post_url") or ""),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    brand = Path(os.environ["BRAND_DIR"]).resolve()
    backup_root = brand / "data" / "media" / "_migrated_backup"

    conn = db.connect()
    db.migrate(conn)
    repo = RecipeRepository(conn)

    updated, skipped = [], []
    for row in repo.list_recipes():
        meta = _load_backup_meta(backup_root, row.id)
        if meta is None:
            skipped.append((row.id, "no backup metadata"))
            continue
        fields = _ig_fields(meta)
        if not any(fields.values()):
            skipped.append((row.id, "no IG data in metadata"))
            continue
        status = {ch: dict(v) for ch, v in row.publish_status.items()}
        ig = dict(status.get("ig", {}))
        ig.update(fields)
        ig.setdefault("state", "")
        status["ig"] = ig
        if status != row.publish_status:
            if args.apply:
                repo.set_publish_status(row.id, status)
            updated.append((row.id, bool(fields["reel_url"]), len(fields["caption"])))
        else:
            skipped.append((row.id, "already current"))

    conn.close()
    mode = "APPLIED" if args.apply else "DRY-RUN"
    logger.info("=== %s ===", mode)
    for rid, has_reel, clen in updated:
        logger.info(
            "ig  %-38.38s reel=%s caption=%dch", rid, "yes" if has_reel else "-", clen
        )
    for rid, why in skipped:
        logger.info("skip %-38.38s (%s)", rid, why)
    logger.info("updated=%d skipped=%d", len(updated), len(skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
