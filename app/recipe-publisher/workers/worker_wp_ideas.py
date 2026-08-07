"""Safety-net worker: drafts approved content ideas via the CrewAI pipeline.

Picks up ideas with status='approved' and no wp_url set -- including ones
auto-approved directly by `lib/crew/scout.py`'s auto-approve, which never
goes through the API's PATCH endpoint at all -- and drafts each via the
CrewAI strategist->writer->quality-editor pipeline
(`scripts/crewai_content_pipeline.py --idea-id <id>`), invoked as a
subprocess. Never publishes live: a real run only ever creates a WordPress
DRAFT (`status: "draft"` hardcoded inside `create_wp_draft`).

`create_wp_draft` (inside the subprocess) already updates content_ideas'
status/wp_result on success; on failure the subprocess itself sets
write_failed/validation_failed where it can identify the cause, or leaves
the idea at "drafting" (this worker's own claim state, see below) for the
revert logic to catch. This worker atomically claims each idea via
`ideas_db.claim_idea_for_drafting` (status='approved' -> 'drafting') before
subprocess-invoking it -- the sole guard preventing this worker and the
ideas_api.py background trigger from both drafting the same idea
concurrently -- and reverts back to "approved" if the subprocess never
reached its own terminal status, so a stuck claim is always retried.

Primary trigger is the ideas_api.py approval hook (BackgroundTasks). This
worker is a cron safety net for ideas where that hook crashed, plus the
sole consumer of ideas auto-approved outside the API (Part A's
auto-approve in scout.py).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

# Add the repo root to sys.path so lib/ is importable
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from lib import ideas_db

log = logging.getLogger(__name__)


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


def _revert_if_still_drafting(idea_id: str) -> None:
    """After a failed subprocess run, un-stick the idea if it's still parked
    at status='drafting' -- meaning the subprocess crashed/timed out/errored
    before it ever reached its own write_failed/validation_failed
    classification. Reverting to 'approved' makes it a valid retry
    candidate for the next sweep of this same worker. If the subprocess DID
    reach its own classification (or something else moved the idea in the
    meantime), status is no longer 'drafting' and this is a no-op.
    """
    current = ideas_db.get_idea(idea_id)
    if current is not None and current.get("status") == "drafting":
        ideas_db.update_status(idea_id, "approved")
        log.info(json.dumps({"event": "idea_draft_reverted_to_approved", "idea_id": idea_id}))


def _do_one(idea: dict, *, dry_run: bool) -> str:
    if dry_run:
        log.info(
            json.dumps(
                {
                    "event": "idea_draft_dry_run",
                    "idea_id": idea.get("id"),
                    "topic": idea.get("topic"),
                }
            )
        )
        return "dry_run"

    idea_id = str(idea["id"])

    if not ideas_db.claim_idea_for_drafting(idea_id):
        log.info(json.dumps({"event": "idea_draft_skipped_not_approved", "idea_id": idea_id}))
        return "skipped"

    try:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.crewai_content_pipeline", "--idea-id", idea_id],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        log.error(json.dumps({"event": "idea_draft_timeout", "idea_id": idea_id, "error": str(exc)}))
        _revert_if_still_drafting(idea_id)
        return "error"
    except Exception as exc:
        log.error(
            json.dumps({"event": "idea_draft_subprocess_error", "idea_id": idea_id, "error": str(exc)})
        )
        _revert_if_still_drafting(idea_id)
        return "error"

    if result.returncode == 0:
        log.info(json.dumps({"event": "idea_drafted", "idea_id": idea_id}))
        return "drafted"

    stderr_tail = (result.stderr or "").strip()[-2000:]
    log.error(
        json.dumps(
            {
                "event": "idea_draft_failed",
                "idea_id": idea_id,
                "returncode": result.returncode,
                "stderr": stderr_tail,
            }
        )
    )
    _revert_if_still_drafting(idea_id)
    return "error"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Draft approved ideas via the CrewAI pipeline")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--idea-id")
    p.add_argument("--brand-id", default=os.getenv("BRAND_ID", "persona"))
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='{"time":"%(asctime)s","level":"%(levelname)s","msg":%(message)s}',
    )

    targets = _targets(
        brand_id=args.brand_id,
        limit=args.limit,
        idea_id=args.idea_id,
    )

    if not targets:
        log.info(json.dumps({"event": "no_approved_ideas_to_draft"}))
        return 0

    results: dict[str, int] = {"drafted": 0, "error": 0, "dry_run": 0, "skipped": 0}
    for idea in targets:
        outcome = _do_one(idea, dry_run=args.dry_run)
        results[outcome] = results.get(outcome, 0) + 1

    log.info(json.dumps({"summary": results}))
    return 0 if results.get("error", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
