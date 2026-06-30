"""Worker — content-idea carousel.

Reads approved content ideas from Supabase, generates a 4-slide IG carousel
via Gemini + Imagen/Pexels, publishes to Instagram, and marks the idea done.

This worker does NOT use _base.py (recipe-DB-coupled). It has its own
arg parsing, bootstrap, and loop — sharing only the generator + publisher layers.

    python -m workers.worker_content_carousel             # dry-run plan
    python -m workers.worker_content_carousel --apply     # generate + publish
    python -m workers.worker_content_carousel --apply --idea-id <uuid>
    python -m workers.worker_content_carousel --health-check
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

# ── path bootstrap ────────────────────────────────────────────────────────────
_rp_root = Path(__file__).resolve().parent.parent   # recipe-publisher/
_sa_root = _rp_root.parent                          # social-automation/
for _p in (_rp_root, _sa_root):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from lib.local_env import load_local_env  # noqa: E402
from lib import ideas_db                  # noqa: E402

load_local_env()

logger = logging.getLogger("workers.content_carousel")


# ── helpers ───────────────────────────────────────────────────────────────────

def _slug(topic: str) -> str:
    """URL-safe slug from topic — used as filename prefix."""
    s = topic.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60]


def _health() -> bool:
    missing = [
        k for k in (
            "GEMINI_API_KEY", "FB_PAGE_TOKEN", "IG_ACCOUNT_ID",
            "WP_URL", "WP_USER", "WP_APP_PASSWORD",
        )
        if not os.environ.get(k)
    ]
    if missing:
        logger.error("missing env vars: %s", ", ".join(missing))
        return False
    logger.info("health-check OK")
    return True


def _targets(brand_id: str, idea_id: str | None) -> list[dict[str, Any]]:
    """Approved ideas that haven't been posted yet (status='approved')."""
    rows = ideas_db.list_ideas(status="approved", brand_id=brand_id)
    if idea_id:
        rows = [r for r in rows if str(r.get("id")) == idea_id]
    return rows


def _slides_dir(idea_id: str) -> Path:
    brand = os.environ.get("BRAND_DIR")
    base = Path(brand) if brand else _rp_root
    return base / "data" / "media" / "content_carousels" / idea_id


def _save_slides(idea_id: str, slides: list) -> Path:
    """Write slide JPEGs to disk. Returns the folder path."""
    folder = _slides_dir(idea_id)
    folder.mkdir(parents=True, exist_ok=True)
    for i, img in enumerate(slides, 1):
        data: bytes = getattr(img, "bytes_", None) or b""
        (folder / f"slide_{i}.jpg").write_bytes(data)
    logger.info("saved %d slides to %s", len(slides), folder)
    return folder


def _do_one(idea: dict[str, Any], *, dry_run: bool) -> str:
    """Generate carousel slides + slideshow video, publish IG carousel + FB video."""
    from generators.content_carousel import generate_content_slides
    from generators.video_slideshow import make_slideshow
    from publishers.instagram import publish_content_carousel
    from publishers.facebook import publish_content_video_to_facebook

    topic: str = idea.get("topic") or ""
    idea_id = str(idea["id"])
    slug = _slug(topic)

    logger.info("idea=%s topic=%r dry_run=%s", idea_id, topic, dry_run)

    slides, caption = generate_content_slides(idea)
    logger.info("generated %d slides", len(slides))

    # Always save slides to disk (preview + video source).
    folder = _save_slides(idea_id, slides)

    # 1:1 for IG carousel source (not used as video on IG).
    # 4:5 for FB feed/Reels — avoids side-cropping in the portrait player.
    from generators.video_slideshow import _FB_HEIGHT, _TARGET_SIZE
    video_path = folder / "slideshow_fb.mp4"
    make_slideshow(folder, video_path, width=_TARGET_SIZE, height=_FB_HEIGHT)
    logger.info("FB slideshow created: %s", video_path)

    if dry_run:
        logger.info("[dry-run] slides+video saved to %s", folder)
        logger.info("[dry-run] caption preview: %s", caption[:120])
        return f"dry-run:saved_to:{folder}"

    ig_result = publish_content_carousel(slides, caption=caption, slug=slug)
    logger.info("IG published idea=%s permalink=%s", idea_id, ig_result.permalink)

    try:
        fb_result = publish_content_video_to_facebook(video_path, caption=caption)
        logger.info("FB video published idea=%s permalink=%s", idea_id, fb_result.permalink)
        fb_permalink = fb_result.permalink
    except Exception:
        logger.exception("FB video publish failed for idea=%s — IG already posted", idea_id)
        fb_permalink = None

    ideas_db.update_status(idea_id, "social_done")
    return f"ig:{ig_result.permalink} fb:{fb_permalink}"


# ── entry point ───────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workers.content_carousel")
    parser.add_argument("--apply", action="store_true", help="generate + publish (default: dry-run)")
    parser.add_argument("--idea-id", default=None, help="restrict to one idea UUID")
    parser.add_argument("--limit", type=int, default=0, help="cap target count (0 = all)")
    parser.add_argument("--health-check", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.health_check:
        return 0 if _health() else 1

    dry_run = not args.apply
    brand_id = Path(os.environ.get("BRAND_DIR", "")).name or "dogfoodandfun"
    targets = _targets(brand_id, args.idea_id)

    if args.limit:
        targets = targets[: args.limit]

    if not targets:
        logger.info("no approved ideas found — nothing to do")
        return 0

    logger.info(
        "content-carousel start brand=%s targets=%d dry_run=%s",
        brand_id, len(targets), dry_run,
    )

    ok = 0
    for idea in targets:
        try:
            outcome = _do_one(idea, dry_run=dry_run)
            logger.info("done idea=%s outcome=%s", idea.get("id"), outcome)
            ok += 1
        except Exception:
            logger.exception("FAILED idea=%s topic=%r", idea.get("id"), idea.get("topic"))

    logger.info("content-carousel done ok=%d/%d", ok, len(targets))
    return 0 if ok == len(targets) else 1


if __name__ == "__main__":
    sys.exit(main())
