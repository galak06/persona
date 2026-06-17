"""Worker D — publish.

The final stage. A recipe with a composed reel AND an operator-supplied audio
track gets published: mux audio → promote the WP draft live → push IG reel, FB
reel, FB page post, and Pinterest pins. Reuses the proven, folder-driven
``scripts.publish_prepared.publish_one`` (PDF skipped — Worker A owns it).

Audio is the one input no worker can produce (the operator drops ``audio.mp3``
into the campaign folder). A pre-apply pass detects it and records
``audio_ready_at`` so Worker D's poll predicate stays pure-DB:

    reel_created_at AND audio_ready_at AND no social_published_at

    python -m workers.worker_publish                   # dry-run plan
    python -m workers.worker_publish --apply --limit 1 # publish one
    python -m workers.worker_publish --health-check     # FB/IG tokens → 0/1
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from recipe_db.models import ContentStatus, RecipeRow
from recipe_db.repository import RecipeRepository

from workers._base import run_worker
from workers._folder import campaign_folder

logger = logging.getLogger("workers.publish")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _detect_audio(repo: RecipeRepository) -> None:
    """Pre-apply pass: rows with a reel but no audio yet → check the folder for
    the operator's audio track and record ``audio_ready_at`` when present."""
    from scripts.publish_prepared import _resolve_audio_path

    for row in repo.list_recipes():
        if (
            row.reel_created_at
            and not row.audio_ready_at
            and _resolve_audio_path(campaign_folder(row)) is not None
        ):
            repo.set_audio_ready(row.id, _now_iso())
            logger.info("audio detected for %s", row.id)


def _targets(
    repo: RecipeRepository, seeds: list[str], limit: int
) -> list[RecipeRow]:
    """Rows with a reel + detected audio, not yet socially published."""
    rows = [
        r
        for r in repo.list_recipes()
        if r.reel_created_at
        and r.audio_ready_at
        and not r.social_published_at
        and (not seeds or r.id in seeds)
    ]
    rows.sort(key=lambda r: r.id)
    return rows[:limit] if limit else rows


def _record_social_urls(repo: RecipeRepository, row: RecipeRow) -> None:
    """Best-effort: copy IG/FB reel permalinks from the (moved) folder metadata
    into publish_status so the viewer shows the social badges."""
    from scripts.publish_prepared import _read_metadata

    meta = _read_metadata(campaign_folder(row))
    current = repo.get_recipe(row.id) or row
    status = {ch: dict(v) for ch, v in current.publish_status.items()}
    ig = meta.get("ig_reel_permalink") or ""
    fb = meta.get("fb_reel_permalink") or ""
    if ig:
        status["ig"] = {"state": "published", "url": ig, "at": _now_iso()}
    if fb:
        status["fb"] = {"state": "published", "url": fb, "at": _now_iso()}
    if ig or fb:
        repo.set_publish_status(row.id, status)


def _do_one(repo: RecipeRepository, row: RecipeRow) -> str:
    """Publish via publish_one (PDF skipped), then record the DB markers."""
    from scripts.publish_prepared import publish_one

    folder = campaign_folder(row)
    if not publish_one(folder, dry_run=False, skip_pdf=True):
        return "publish-failed"
    repo.set_social_published(row.id, _now_iso())
    try:
        _record_social_urls(repo, row)
    except Exception as exc:
        logger.warning("social-url sync failed for %s: %s", row.id, exc)
    repo.set_content_status(row.id, ContentStatus.PUBLISHED)
    return "published"


def _health() -> bool:
    """FB/IG publishing credentials present."""
    missing = [k for k in ("FB_PAGE_TOKEN", "IG_ACCOUNT_ID") if not os.environ.get(k)]
    if missing:
        logger.error("missing env: %s", ", ".join(missing))
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    return run_worker(
        "publish",
        targets_fn=_targets,
        do_one_fn=_do_one,
        health_fn=_health,
        pre_apply_fn=_detect_audio,
        argv=argv,
    )


if __name__ == "__main__":
    import sys

    sys.exit(main())
