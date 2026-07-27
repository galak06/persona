"""Worker D — publish.

The final stage. A recipe with a composed reel AND an operator-supplied audio
track gets published end-to-end:

  1. Upload audio.mp3 → WP Media Library, inject audio player into WP post.
  2. Mux reel_src/ frames + audio.mp3 → reel_final.mp4.
  3. Publish reel_final.mp4 to Instagram Reels.
  4. Publish reel_final.mp4 to Facebook Reels (with fb_caption.txt).

Audio is the one input no worker can produce (operator drops ``audio.mp3``
into the campaign folder). A pre-apply pass detects it and records
``audio_ready_at`` so Worker D's poll predicate stays pure-DB:

    reel_created_at AND audio_ready_at AND no social_published_at

The WP audio injection is guarded by ``wp_audio_updated_at`` — if this worker
is ever re-triggered the MP3 is never re-uploaded to WP.

    python -m workers.worker_publish                   # dry-run plan
    python -m workers.worker_publish --apply --limit 1 # publish one
    python -m workers.worker_publish --health-check     # FB/IG tokens → 0/1
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from recipe_db.models import ContentStatus, RecipeRow
from recipe_db.repository import RecipeRepository

from workers._base import run_worker
from workers._folder import load_frames, reel_folder, rehydrate_recipe

logger = logging.getLogger("workers.publish")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# WP audio injection
# ---------------------------------------------------------------------------


def _wp_post_id_from_slug(slug: str) -> int | None:
    from lib.sessions.wp_client import wp_client  # type: ignore[import-untyped]

    with wp_client() as client:
        resp = client.get("/wp-json/wp/v2/posts", params={"slug": slug, "_fields": "id"})
    resp.raise_for_status()
    posts = resp.json()
    return int(posts[0]["id"]) if posts else None


def _inject_wp_audio(row: RecipeRow, folder: Path) -> None:
    """Upload audio.mp3 to WP and inject the player block. Raises on failure."""
    from lib.recipe_card.wp_audio import (  # type: ignore[import-untyped]
        inject_audio_player,
        upload_audio,
    )

    audio_path = folder / "audio.mp3"
    if not audio_path.exists():
        raise FileNotFoundError(f"audio.mp3 not found in {folder}")
    if not row.wp_url:
        raise ValueError(f"{row.id}: no wp_url")

    slug = row.wp_url.rstrip("/").rsplit("/", 1)[-1]
    post_id = _wp_post_id_from_slug(slug)
    if post_id is None:
        raise ValueError(f"{row.id}: WP post not found for slug {slug!r}")

    media_id, source_url = upload_audio(audio_path.read_bytes(), f"{row.id}.mp3")
    inject_audio_player(post_id, media_id, source_url)
    logger.info("%s: WP audio injected (post=%d, media=%d)", row.id, post_id, media_id)


# ---------------------------------------------------------------------------
# Mux + social publish
# ---------------------------------------------------------------------------


def _mux_reel(folder: Path) -> Path:
    """Compose reel_src/ frames + audio.mp3 into reel_final.mp4. Idempotent."""
    from generators.reel import compose_reel

    out = folder / "reel_final.mp4"
    if out.exists() and out.stat().st_size > 0:
        logger.info("reel_final.mp4 already exists — skipping mux")
        return out

    frames = load_frames(folder / "reel_src")
    if not frames:
        raise FileNotFoundError(f"no reel_src frames in {folder}")

    compose_reel(frames, out, audio_path=folder / "audio.mp3")
    logger.info("%s: reel_final.mp4 muxed", folder.name)
    return out


def _publish_social(
    repo: RecipeRepository, row: RecipeRow, folder: Path
) -> dict[str, dict[str, str]]:
    """Publish reel_final.mp4 to IG and FB. Writes permalinks to metadata.json
    and updates publish_status in the DB. Returns the publish_status dict."""
    from publishers.facebook import publish_reel_to_facebook  # type: ignore[import-untyped]
    from publishers.instagram import publish_reel_to_instagram  # type: ignore[import-untyped]

    recipe = rehydrate_recipe(row)
    reel_path = folder / "reel_final.mp4"
    now = _now_iso()

    fb_caption_path = folder / "fb_caption.txt"
    fb_caption = fb_caption_path.read_text(encoding="utf-8").strip() if fb_caption_path.exists() else None

    ig_result = publish_reel_to_instagram(recipe, reel_path)
    ig_url = ig_result.permalink or ""
    logger.info("%s: IG reel published → %s", row.id, ig_url)

    fb_result = publish_reel_to_facebook(recipe, reel_path, description=fb_caption)
    fb_url = (
        fb_result.permalink
        or (f"https://www.facebook.com/reel/{fb_result.video_id}" if fb_result.video_id else "")
    )
    logger.info("%s: FB reel published → %s", row.id, fb_url)

    publish_status = {
        **row.publish_status,  # preserve existing wp / other channels
        "ig": {"state": "published", "url": ig_url, "at": now},
        "fb": {"state": "published", "url": fb_url, "at": now},
    }

    # Persist to DB (updates ig_url + fb_url columns the frontend reads)
    repo.set_publish_status(row.id, publish_status)

    # Write back to metadata.json for local reference
    meta_path = folder / "metadata.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["ig_reel_permalink"] = ig_url
        meta["fb_reel_permalink"] = fb_url
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return publish_status


# ---------------------------------------------------------------------------
# Audio detection pre-pass
# ---------------------------------------------------------------------------


def _detect_audio(repo: RecipeRepository) -> None:
    """Stamp audio_ready_at when audio.mp3 appears in the reel folder."""
    for row in repo.list_recipes():
        if row.reel_created_at and not row.audio_ready_at:
            folder = reel_folder(row)
            if (folder / "audio.mp3").exists():
                repo.set_audio_ready(row.id, _now_iso())
                logger.info("audio detected for %s", row.id)


# ---------------------------------------------------------------------------
# Worker wiring
# ---------------------------------------------------------------------------


def _targets(
    repo: RecipeRepository, seeds: list[str], limit: int
) -> list[RecipeRow]:
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


def _do_one(repo: RecipeRepository, row: RecipeRow) -> str:
    folder = reel_folder(row)

    # Step 1: inject MP3 into WP post (guarded by wp_audio_updated_at)
    if not (row.wp_audio_updated_at or "").strip():
        try:
            _inject_wp_audio(row, folder)
            repo.set_wp_audio_updated(row.id, _now_iso())
        except Exception as exc:
            logger.warning("%s: WP audio injection failed — %s", row.id, exc)

    # Step 2: mux audio into reel
    reel_path = _mux_reel(folder)
    logger.info("%s: reel ready at %s", row.id, reel_path)

    # Step 3: publish to IG + FB
    _publish_social(repo, row, folder)

    repo.set_social_published(row.id, _now_iso())
    repo.set_content_status(row.id, ContentStatus.PUBLISHED)
    return "published"


def _health() -> bool:
    missing = [k for k in ("FB_PAGE_TOKEN", "IG_ACCOUNT_ID", "FB_PAGE_ID") if not os.environ.get(k)]
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
