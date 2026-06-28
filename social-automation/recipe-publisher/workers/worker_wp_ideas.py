"""Safety-net worker: publishes approved content ideas to WordPress.

Picks up ideas with status='approved' and no wp_url set,
claims them (status→wp_draft), publishes, then marks wp_published.
Primary trigger is the ideas_api.py approval hook (BackgroundTasks).
This worker is a cron safety net for ideas where that hook crashed.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Add the repo root to sys.path so lib/ is importable
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from lib import ideas_db
from publishers.wordpress_ideas import publish_idea_to_wordpress

log = logging.getLogger(__name__)


def _load_enrichment_cache(brand_dir: str) -> dict[str, dict]:
    cache_path = Path(brand_dir) / "state" / "enrichment_cache.json"
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {e["topic"].lower(): e for e in data if "topic" in e}
        if isinstance(data, dict):
            return {k.lower(): v for k, v in data.items()}
        return {}
    except Exception as exc:
        log.warning('"enrichment_cache load failed: %s"', exc)
        return {}


def _targets(
    *,
    brand_id: str,
    limit: int,
    idea_id: str | None,
) -> list[dict]:
    rows = ideas_db.list_ideas(status="approved", brand_id=brand_id, limit=limit)
    filtered = [r for r in rows if r.get("wp_url") is None]
    if idea_id is not None:
        filtered = [r for r in filtered if str(r.get("id")) == idea_id]
    return filtered


def _do_one(
    idea: dict,
    enrichment_cache: dict[str, dict],
    *,
    dry_run: bool,
) -> str:
    enrichment = enrichment_cache.get(idea.get("topic", "").lower())

    if dry_run:
        log.info(
            '"dry_run: would publish idea id=%s topic=%s"',
            idea.get("id"),
            idea.get("topic"),
        )
        return "dry_run"

    ideas_db.update_status(idea["id"], "wp_draft")
    try:
        result = publish_idea_to_wordpress(idea, enrichment)
        ideas_db.set_wp_result(idea["id"], result.post_id, result.permalink)
        ideas_db.update_status(idea["id"], "wp_published")
        log.info(
            '"published idea id=%s post_id=%s permalink=%s"',
            idea.get("id"),
            result.post_id,
            result.permalink,
        )
        return "published"
    except Exception as exc:
        log.error('"publish failed idea id=%s error=%s"', idea.get("id"), exc)
        ideas_db.update_status(idea["id"], "approved")
        return "error"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Publish approved ideas to WordPress")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--idea-id")
    p.add_argument("--brand-id", default=os.getenv("BRAND_ID", "dogfoodandfun"))
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='{"time":"%(asctime)s","level":"%(levelname)s","msg":%(message)s}',
    )

    brand_dir = os.getenv("BRAND_DIR", "")
    enrichment_cache = _load_enrichment_cache(brand_dir)
    targets = _targets(
        brand_id=args.brand_id,
        limit=args.limit,
        idea_id=args.idea_id,
    )

    if not targets:
        log.info('"no approved ideas to publish"')
        return 0

    results: dict[str, int] = {"published": 0, "error": 0, "dry_run": 0}
    for idea in targets:
        outcome = _do_one(idea, enrichment_cache, dry_run=args.dry_run)
        results[outcome] = results.get(outcome, 0) + 1

    log.info(json.dumps({"summary": results}))
    return 0 if results.get("error", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
