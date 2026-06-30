"""Worker B — post-images.

A recipe with a WordPress post but no carousel slides gets its images generated.
The image model runs ONCE and produces two branded variants from the same base
frames: the POST slides (Nalla seal on the hero) and the REEL frames (the
@handle pill on the hero — the badge never goes on the reel). Both are written
to disk so Worker C can compose the reel without regenerating images.

Poll predicate (independent + idempotent):
    wp_url AND no slides_created_at

Writes ``slides/slide_N.jpg`` (post) + ``reel_src/slide_N.jpg`` (reel frames),
then records ``slides_created_at`` + ``slides_count`` → Worker C polls on those.

    python -m workers.worker_post_images                   # dry-run plan
    python -m workers.worker_post_images --apply --limit 1 # generate one
    python -m workers.worker_post_images --health-check
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository

from workers._base import run_worker
from workers._folder import badge_path, reel_in_review_folder, save_frames, save_images

logger = logging.getLogger("workers.post_images")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _targets(
    repo: RecipeRepository, seeds: list[str], limit: int
) -> list[RecipeRow]:
    """Rows with a WP post but no carousel slides yet. Idempotent: once
    ``slides_created_at`` is set the row is no longer selected."""
    rows = [
        r
        for r in repo.list_recipes()
        if r.content_created_at
        and not r.slides_created_at
        and (not seeds or r.id in seeds)
    ]
    rows.sort(key=lambda r: r.id)
    return rows[:limit] if limit else rows


def _do_one(repo: RecipeRepository, row: RecipeRow) -> str:
    """Generate carousel images once; save post slides + reel frames; mark done."""
    from generators.carousel import generate_post_and_reel_slides
    from generators.carousel_drafter import ensure_carousel_json
    from generators.seeds import load_seeds

    seed = next((s for s in load_seeds() if s.id == row.id), None)
    if seed is None:
        logger.warning("%s: no seed in seeds.json — cannot build carousel", row.id)
        return "no-seed"

    ensure_carousel_json(seed, force=False)
    post_slides, reel_frames = generate_post_and_reel_slides(
        seed_id=row.id,
        recipe_title=row.display_name or row.name,
        badge_path=badge_path(),
    )

    folder = reel_in_review_folder(row)
    count = save_images(post_slides, folder / "slides")
    save_frames(reel_frames, folder / "reel_src")
    repo.set_slides(row.id, count, _now_iso())
    return f"slides={count}"


def _health() -> bool:
    """At least one image-provider key present (Gemini drives Nano Pro/Imagen)."""
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("PEXELS_API_KEY"):
        return True
    logger.error("no image-provider key (GEMINI_API_KEY / PEXELS_API_KEY)")
    return False


def main(argv: list[str] | None = None) -> int:
    return run_worker(
        "post_images",
        targets_fn=_targets,
        do_one_fn=_do_one,
        health_fn=_health,
        argv=argv,
    )


if __name__ == "__main__":
    import sys

    sys.exit(main())
