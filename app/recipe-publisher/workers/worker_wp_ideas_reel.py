"""Reel composer: builds two platform-tuned Reels from 5 per-beat source
images (one per `lib.crew.reels.models.ReelBeat`) + that beat's overlay text.

The 5 source images come from one of two places, decided by the caller
(`scripts/crewai_reels_pipeline.py`) before invoking this worker -- either 5
distinct OpenArt-generated images (one per beat's own `image_prompt`,
`source="openart"`) or the WP post's single existing hero image repeated 5
times (`source="fallback"`, reached only when OpenArt fails for any reason,
including running out of credits). This worker doesn't need to know which
case it's in beyond the `--source` label it passes straight through to the
DB write -- it always receives exactly 5 image paths, positionally matched
to the 5 beats in `--plan`, and treats them identically either way.

Named to pair with `worker_wp_ideas.py` (same `content_ideas` domain), and
deliberately distinct from the existing (misnamed) `worker_post_reels.py`.

Flow: for each of the 5 (image, beat) pairs, center-crop that beat's image
to 9:16 (fills the frame with real photo content instead of
`compose_reel`'s blur-letterbox fallback; a no-op for images already
narrower than 9:16, e.g. OpenArt's own 9:16 output) -> for each platform,
overlay each beat at that platform's safe-zone tuning -> `compose_reel()`
into a silent 9:16 mp4 -> scan the combined text with the same
`lib.medical_claims_validator.find_banned_claims()` the WP body itself uses
(flagged, not blocked -- a human reviews every reel regardless) ->
`ideas_db.set_reel_pending_review(...)`. This worker owns the full terminal
DB write, same pattern `lib.crew.draft.create_wp_draft` already uses for
the drafting pipeline.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
from pathlib import Path

# Add the repo root to sys.path so lib/ is importable, and this directory's
# parent to sys.path so `generators.*` resolves -- same pattern
# worker_wp_ideas.py already uses for the lib/ half of this.
_RECIPE_PUBLISHER_ROOT = Path(__file__).resolve().parents[1]
_ROOT = _RECIPE_PUBLISHER_ROOT.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_RECIPE_PUBLISHER_ROOT))

from generators.reel import compose_reel
from generators.text_overlay import OverlaySpec, apply_overlay
from PIL import Image

from lib import ideas_db
from lib.medical_claims_validator import find_banned_claims

log = logging.getLogger(__name__)

# IG's value is established (text_overlay.py's own documented Reels calling
# convention, clears IG's bottom-right like/comment/share icons). FB's is a
# starting default, NOT yet visually confirmed against a real FB Reels post
# -- see the plan's verification section. Both platforms' Reels UI chrome
# sits in a similar bottom-right zone, so starting from the same value is a
# reasonable default pending that confirmation, not a guess pulled from
# nowhere.
_IG_HEADLINE_Y_PCT = 0.60
_FB_HEADLINE_Y_PCT = 0.60  # TODO: confirm against a real FB Reels post


def _center_crop_to_9_16(image_bytes: bytes) -> bytes:
    """Crop a (typically 16:9) source image down to a 9:16 vertical strip,
    full height, centered horizontally -- fills the whole Reel frame with
    real photo content instead of `compose_reel`'s blur-letterbox fallback
    for far-off-ratio sources. First-pass default; see the plan's "Known
    gap" section -- expected to be revisited once real output is reviewed.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    target_w = round(h * 9 / 16)
    if target_w >= w:
        return image_bytes  # already narrower than 9:16 -- nothing to crop
    left = (w - target_w) // 2
    cropped = img.crop((left, 0, left + target_w, h))
    out = io.BytesIO()
    cropped.save(out, "JPEG", quality=95)
    return out.getvalue()


def _render_platform_reel(
    cropped_images: list[bytes],
    beats: list[dict],
    *,
    headline_y_pct: float,
    output_path: Path,
) -> Path:
    slide_bytes = [
        apply_overlay(
            image,
            OverlaySpec(headline=beat["headline"], subcopy=beat["subcopy"]),
            headline_y_pct=headline_y_pct,
        )
        for image, beat in zip(cropped_images, beats, strict=True)
    ]
    return compose_reel(slide_bytes, output_path, audio_path=None)


def _do_one(
    *, idea_id: str, images_path: Path, plan_path: Path, slug: str, source: str
) -> str:
    plan = json.loads(plan_path.read_text())
    beats: list[dict] = plan["beats"]
    ig_caption: str = plan["ig_caption"]
    fb_caption: str = plan["fb_caption"]

    image_paths: list[str] = json.loads(images_path.read_text())
    if len(image_paths) != len(beats):
        log.error(
            json.dumps(
                {
                    "event": "reel_image_beat_count_mismatch",
                    "idea_id": idea_id,
                    "images": len(image_paths),
                    "beats": len(beats),
                }
            )
        )
        return "error"
    cropped_images = [_center_crop_to_9_16(Path(p).read_bytes()) for p in image_paths]

    brand_dir = Path(os.environ.get("BRAND_DIR", ".")).resolve()
    reels_dir = brand_dir / "state" / "reels_pending"
    reels_dir.mkdir(parents=True, exist_ok=True)

    ig_path = _render_platform_reel(
        cropped_images,
        beats,
        headline_y_pct=_IG_HEADLINE_Y_PCT,
        output_path=reels_dir / f"{slug}_ig.mp4",
    )
    fb_path = _render_platform_reel(
        cropped_images,
        beats,
        headline_y_pct=_FB_HEADLINE_Y_PCT,
        output_path=reels_dir / f"{slug}_fb.mp4",
    )

    combined_text = "\n".join(
        [ig_caption, fb_caption] + [f"{b['headline']}\n{b['subcopy']}" for b in beats]
    )
    flags = find_banned_claims(combined_text)

    ok = ideas_db.set_reel_pending_review(
        idea_id,
        # Relative to BRAND_DIR, not absolute -- confirmed live: this
        # worker runs on the host (absolute host paths), but the API
        # server that later serves/deletes these files runs in a container
        # where the same brands/ tree is bind-mounted at a different
        # absolute path. A path stored relative to BRAND_DIR resolves
        # correctly under either process's own BRAND_DIR, same convention
        # `api/ideas_api.py::_slides_dir` already uses for carousel slides.
        ig_video_path=str(ig_path.relative_to(brand_dir)),
        fb_video_path=str(fb_path.relative_to(brand_dir)),
        ig_caption=ig_caption,
        fb_caption=fb_caption,
        source=source,
        validation_flags=flags or None,
    )
    if not ok:
        log.error(json.dumps({"event": "reel_pending_review_write_failed", "idea_id": idea_id}))
        return "error"

    log.info(
        json.dumps(
            {
                "event": "reel_composed",
                "idea_id": idea_id,
                "source": source,
                "validation_flags": flags,
            }
        )
    )
    return "composed"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Compose a Reel from 5 per-beat source images")
    p.add_argument("--idea-id", required=True)
    p.add_argument("--images", required=True, type=Path, help="JSON file: list of 5 image paths")
    p.add_argument("--plan", required=True, type=Path)
    p.add_argument("--slug", required=True)
    p.add_argument("--source", required=True, choices=("openart", "fallback"))
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='{"time":"%(asctime)s","level":"%(levelname)s","msg":%(message)s}',
    )

    outcome = _do_one(
        idea_id=args.idea_id,
        images_path=args.images,
        plan_path=args.plan,
        slug=args.slug,
        source=args.source,
    )
    return 0 if outcome == "composed" else 1


if __name__ == "__main__":
    sys.exit(main())
