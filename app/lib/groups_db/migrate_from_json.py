"""One-time copy of groups_tracker.json → groups.db. Idempotent; keeps the JSON.

    python -m lib.groups_db.migrate_from_json            # dry-run (counts only)
    python -m lib.groups_db.migrate_from_json --apply     # write groups.db

Copy-only: the source JSON is never modified or deleted (additive-only rule).
Re-running is safe — groups are upserted by group_url.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter

logger = logging.getLogger("groups_db.migrate_from_json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write to groups.db")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from lib.config import settings
    from lib.groups_db.db import connect, migrate, resolve_groups_db_path
    from lib.groups_db.repository import GroupsRepository

    assert settings.paths is not None
    src = settings.paths.groups_tracker
    if not src.exists():
        logger.error("no tracker found at %s", src)
        return 1

    groups = json.loads(src.read_text(encoding="utf-8"))
    by_status = dict(Counter(g.get("status", "?") for g in groups))
    logger.info("%d groups in %s — by status: %s", len(groups), src, by_status)

    if not args.apply:
        logger.info("DRY-RUN — pass --apply to write %s", resolve_groups_db_path())
        return 0

    conn = connect()
    migrate(conn)
    GroupsRepository(conn).save_all(groups)
    conn.close()
    logger.info("migrated %d groups into %s", len(groups), resolve_groups_db_path())
    return 0


if __name__ == "__main__":
    sys.exit(main())
