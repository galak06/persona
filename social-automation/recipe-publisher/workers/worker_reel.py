"""Worker C — reel.

A recipe whose carousel images exist but has no reel video gets its silent reel
composed. It stitches the UN-badged ``reel_src/`` frames (saved by Worker B) into
a 9:16 ``source.mp4`` via ffmpeg — the badge never reaches the reel. Audio is
added later (operator drop → Worker D); this stage is silent.

Poll predicate (independent + idempotent):
    slides_created_at AND no reel_created_at

Writes ``source.mp4`` then records ``reel_created_at`` → Worker D polls on that.

    python -m workers.worker_reel                   # dry-run plan
    python -m workers.worker_reel --apply --limit 1 # compose one
    python -m workers.worker_reel --health-check     # ffmpeg present → 0/1
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime

from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository

from workers._base import run_worker
from workers._folder import load_frames, reel_in_review_folder

logger = logging.getLogger("workers.reel")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _targets(
    repo: RecipeRepository, seeds: list[str], limit: int
) -> list[RecipeRow]:
    """Rows with carousel slides but no reel yet. Idempotent: once
    ``reel_created_at`` is set the row is no longer selected."""
    rows = [
        r
        for r in repo.list_recipes()
        if r.slides_created_at
        and not r.reel_created_at
        and (not seeds or r.id in seeds)
    ]
    rows.sort(key=lambda r: r.id)
    return rows[:limit] if limit else rows


def _do_one(repo: RecipeRepository, row: RecipeRow) -> str:
    """Compose the silent reel from the un-badged reel_src frames; mark done."""
    from generators.reel import compose_reel

    folder = reel_in_review_folder(row)
    frames = load_frames(folder / "reel_src")
    if not frames:
        logger.warning("%s: no reel_src frames — cannot compose reel", row.id)
        return "no-frames"

    compose_reel(frames, folder / "source.mp4", audio_path=None)
    repo.set_reel(row.id, _now_iso())
    return "reel"


def _health() -> bool:
    """ffmpeg must be on PATH to compose the reel."""
    if shutil.which("ffmpeg") is not None:
        return True
    logger.error("ffmpeg not found on PATH")
    return False


def main(argv: list[str] | None = None) -> int:
    return run_worker(
        "reel",
        targets_fn=_targets,
        do_one_fn=_do_one,
        health_fn=_health,
        argv=argv,
    )


if __name__ == "__main__":
    import sys

    sys.exit(main())
