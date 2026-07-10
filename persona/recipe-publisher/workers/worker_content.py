"""Worker content — full reel pipeline: content files → slides → silent mp4.

Runs all three stages in one pass. Each stage is guarded by its own DB timestamp
so a partial failure resumes cleanly on the next run.

Stages
~~~~~~
1. Content files  — metadata.json, ig_caption.txt, fb_caption.txt, lyrics.md,
                    recipe_body.html  →  stamps ``content_created_at``
2. Slides         — carousel slides/ + reel_src/ frames via Gemini Imagen
                    →  stamps ``slides_created_at`` + ``slides_count``
3. Reel           — ffmpeg stitches reel_src/ into silent source.mp4
                    →  stamps ``reel_created_at``

Poll predicate (idempotent):
    dog_safe  AND  reel_created_at == ""

Usage
-----
    python -m workers.worker_content                    # dry-run plan
    python -m workers.worker_content --apply --limit 1  # run one
    python -m workers.worker_content --health-check     # check deps → 0/1
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import markdown
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository

from workers._base import run_worker
from workers._folder import (
    badge_path,
    ensure_seed_exported,
    load_frames,
    reel_in_review_folder,
    rehydrate_recipe,
    save_frames,
    save_images,
)
from workers._llm import draft_fb_caption, draft_lyrics, enforce_hashtag_limit

logger = logging.getLogger("workers.content")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------


def _stage_content(repo: RecipeRepository, row: RecipeRow, folder: Path) -> None:
    """Write the 5 content files and stamp content_created_at."""
    from generators.seeds import load_seeds

    ensure_seed_exported(row)
    recipe = rehydrate_recipe(row)
    seed = next((s for s in load_seeds() if s.id == recipe.seed_id), None)
    topic_keywords: list[str] = list(seed.topic_keywords) if seed else []

    ig_caption = enforce_hashtag_limit(recipe.ig_caption)
    fb_caption = draft_fb_caption(recipe)

    meta = {
        "seed_id": recipe.seed_id,
        "slug": recipe.slug,
        "title": recipe.title,
        "tags": list(recipe.tags),
        "topic_keywords": topic_keywords,
        "ig_caption": ig_caption,
        "fb_caption": fb_caption,
        "carousel_slides": [],
        "prepared_at": _now_iso(),
    }
    (folder / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (folder / "ig_caption.txt").write_text(ig_caption, encoding="utf-8")
    (folder / "fb_caption.txt").write_text(fb_caption, encoding="utf-8")
    (folder / "lyrics.md").write_text(draft_lyrics(recipe), encoding="utf-8")
    (folder / "recipe_body.html").write_text(
        markdown.markdown(recipe.body_markdown), encoding="utf-8"
    )
    repo.set_content(row.id, _now_iso())
    logger.info("%s: content files written", row.id)


def _stage_slides(repo: RecipeRepository, row: RecipeRow, folder: Path) -> None:
    """Generate carousel slides + reel frames and stamp slides_created_at."""
    from generators.carousel import generate_post_and_reel_slides
    from generators.carousel_drafter import ensure_carousel_json
    from generators.seeds import load_seeds

    seed = next((s for s in load_seeds() if s.id == row.id), None)
    if seed is None:
        logger.warning("%s: no seed in seeds.json — cannot build carousel", row.id)
        return
    ensure_carousel_json(seed, force=False)
    post_slides, reel_frames = generate_post_and_reel_slides(
        seed_id=row.id,
        recipe_title=row.display_name or row.name,
        badge_path=badge_path(),
    )
    count = save_images(post_slides, folder / "slides")
    save_frames(reel_frames, folder / "reel_src")
    repo.set_slides(row.id, count, _now_iso())
    logger.info("%s: %d slides written", row.id, count)


def _stage_reel(repo: RecipeRepository, row: RecipeRow, folder: Path) -> None:
    """Compose the silent source.mp4 and stamp reel_created_at."""
    from generators.reel import compose_reel

    frames = load_frames(folder / "reel_src")
    if not frames:
        logger.warning("%s: no reel_src frames — cannot compose reel", row.id)
        return
    compose_reel(frames, folder / "source.mp4", audio_path=None)
    repo.set_reel(row.id, _now_iso())
    logger.info("%s: source.mp4 written", row.id)


# ---------------------------------------------------------------------------
# Worker wiring
# ---------------------------------------------------------------------------


def _targets(
    repo: RecipeRepository, seeds: list[str], limit: int
) -> list[RecipeRow]:
    """Dog-safe recipes with no reel yet (picks up any incomplete stage)."""
    rows = [
        r
        for r in repo.list_recipes()
        if r.dog_safe
        and not (r.reel_created_at or "").strip()
        and (not seeds or r.id in seeds)
    ]
    rows.sort(key=lambda r: r.id)
    return rows[:limit] if limit else rows


def _do_one(repo: RecipeRepository, row: RecipeRow) -> str:
    folder = reel_in_review_folder(row)
    if not (row.content_created_at or "").strip():
        _stage_content(repo, row, folder)
    if not (row.slides_created_at or "").strip():
        _stage_slides(repo, row, folder)
    if not (row.reel_created_at or "").strip():
        _stage_reel(repo, row, folder)
    return "content+slides+reel"


def _health() -> bool:
    ok = True
    if not (
        os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    ):
        logger.warning("health-check: no LLM key (GEMINI_API_KEY / ANTHROPIC_API_KEY)")
        ok = False
    if importlib.util.find_spec("markdown") is None:
        logger.warning("health-check: 'markdown' package not importable")
        ok = False
    if shutil.which("ffmpeg") is None:
        logger.warning("health-check: ffmpeg not found on PATH")
        ok = False
    return ok


def main(argv: list[str] | None = None) -> int:
    return run_worker(
        "content",
        targets_fn=_targets,
        do_one_fn=_do_one,
        health_fn=_health,
        argv=argv,
    )


if __name__ == "__main__":
    import sys

    sys.exit(main())
