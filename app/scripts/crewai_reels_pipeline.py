"""WP-post -> IG + FB Reels orchestrator.

Two phases, run together on every invocation:

1. **Detect-publish sweep** -- for `content_ideas` rows at `status='wp_draft'`
   with `wp_post_id` set (scopes this to CrewAI-authored posts only), checks
   the live WordPress REST API; flips to `status='wp_published'` once the
   post is actually live. Always real (a cheap, idempotent status check),
   even under `--dry-run`.
2. **Reel composition** -- for `wp_published` rows with no reel composed
   yet, claims the row, runs the Reel Beats Agent (`lib.crew.reels.agent`/
   `execute`) once for a fixed 5-beat script (each beat: on-screen headline/
   subcopy + an image-generation prompt) plus both platform captions. Tries
   OpenArt (`lib.crew.reels.openart_client`) for 5 distinct per-beat images
   first; any failure there -- including running out of OpenArt credits --
   falls through to reusing the post's own hero image for all 5 beats
   instead. Either way, the same 5 (image, beat) pairs get composed into two
   platform-tuned clips by
   `recipe-publisher/workers/worker_wp_ideas_reel.py` (subprocess -- see
   that module's docstring for why this crosses a tree boundary), which
   scans the combined text with the same
   `lib.medical_claims_validator.find_banned_claims()` the WP body itself
   uses (flagged, not blocked) before the idea lands at `status='social_queued'`
   for human review on the frontend (`Reels.tsx`) -- publishing only happens
   after that approval (`worker_wp_ideas_reel_publish.py`).

`--dry-run`: the detect-publish sweep still runs for real (a cheap, safe
status check). The Beats Agent still makes a real LLM call (same DeepSeek-
calls-either-way caveat as `crewai_content_pipeline.py`) but skips the
OpenArt image calls and the compose subprocess entirely, and makes no
`content_ideas` writes for the composed result -- just prints the plan.

Usage::

    python -m scripts.crewai_reels_pipeline --dry-run
    python -m scripts.crewai_reels_pipeline
    python -m scripts.crewai_reels_pipeline --idea-id <content_ideas id>
"""

from __future__ import annotations

import argparse
import functools
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

import anyio
import httpx

from lib import ideas_db
from lib.crew.context import brand_voice_summary
from lib.crew.reels import build_reels_agent, build_reels_task, execute_reels_crew
from lib.crew.reels.models import ReelPlan
from lib.crew.reels.openart_client import generate_image
from lib.crew.reels.prompts import build_reels_task_description
from lib.local_env import load_brand_env_into_environ, load_local_env
from lib.observability import get_logger
from lib.sessions.wp_client import wp_client

logger = get_logger(__name__)

_REQUIRED_ENV_VARS = ("DEEPSEEK_API_KEY", "WP_URL", "WP_USER", "WP_APP_PASSWORD")
_BODY_TRUNCATE_CHARS = 3000
_RECIPE_PUBLISHER_ROOT = _ENGINE_ROOT / "recipe-publisher"
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _infer_brand_dir() -> Path:
    """Same convention as `scripts/crewai_content_pipeline.py::_infer_brand_dir`."""
    brand_dir_env = os.environ.get("BRAND_DIR")
    if brand_dir_env:
        return Path(brand_dir_env)
    brands_root = _ENGINE_ROOT / "brands"
    candidates = sorted(
        d for d in brands_root.glob("*") if d.is_dir() and not d.name.startswith((".", "_"))
    )
    if len(candidates) == 1:
        return candidates[0]
    raise SystemExit(
        "BRAND_DIR not set and brands/ doesn't have exactly one brand folder -- pass --brand-dir"
    )


def _check_required_env() -> list[str]:
    return [name for name in _REQUIRED_ENV_VARS if not os.environ.get(name, "").strip()]


def _strip_html(raw_html: str) -> str:
    text = _HTML_TAG_RE.sub(" ", raw_html)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _fetch_wp_post(wp_post_id: str) -> dict[str, Any] | None:
    with wp_client() as client:
        resp = client.get(f"/wp-json/wp/v2/posts/{wp_post_id}")
        if resp.status_code != 200:
            return None
        data: dict[str, Any] = resp.json()
        return data


def _fetch_featured_image_bytes(featured_media_id: int) -> bytes | None:
    if not featured_media_id:
        return None
    with wp_client() as client:
        resp = client.get(f"/wp-json/wp/v2/media/{featured_media_id}")
        if resp.status_code != 200:
            return None
        source_url = resp.json().get("source_url")
    if not source_url:
        return None
    r = httpx.get(source_url, timeout=60.0)
    r.raise_for_status()
    return r.content


def _detect_publish_sweep(*, brand_id: str) -> int:
    """Phase 1: for `wp_draft` rows with `wp_post_id` set, check live WP
    status; flip to `wp_published` if now live. Always real -- a cheap,
    idempotent status check, not gated by `--dry-run`. Returns count flipped."""
    rows = ideas_db.list_ideas(status="wp_draft", brand_id=brand_id, limit=500)
    flipped = 0
    for row in rows:
        wp_post_id = row.get("wp_post_id")
        if not wp_post_id:
            continue
        post = _fetch_wp_post(str(wp_post_id))
        if post is None or post.get("status") != "publish":
            continue
        if ideas_db.mark_wp_published(str(row["id"])):
            logger.info("reels_marked_wp_published", idea_id=row["id"])
            flipped += 1
    return flipped


def _try_openart_images(plan: ReelPlan, hero_bytes: bytes) -> list[bytes] | None:
    """Primary path: one OpenArt-generated image per beat, from that beat's
    own `image_prompt`, generated in image2image mode with the WP post's
    hero image as a reference -- confirmed live: without a reference,
    OpenArt has no idea what the brand's actual mascot looks like and
    invents a generic dog instead. Returns `None` on ANY failure -- partial
    results are discarded and the caller falls through entirely to reusing
    the WP post's hero image for all 5 beats, same all-or-nothing
    philosophy the OpenArt video path (which this replaced) used."""
    images: list[bytes] = []
    for index, beat in enumerate(plan.beats):
        try:
            image_bytes = anyio.run(
                functools.partial(generate_image, beat.image_prompt, reference_image=hero_bytes)
            )
        except Exception as exc:
            logger.warning("reels_openart_image_generation_failed", beat_index=index, error=str(exc))
            return None
        images.append(image_bytes)
    return images


def _compose_reel(
    *, idea_id: str, plan: ReelPlan, images: list[bytes], source: str, brand_dir: Path
) -> bool:
    """Write the plan + one image per beat to a temp dir, subprocess-invoke
    the recipe-publisher compose worker, which owns writing the terminal
    `set_reel_pending_review` DB state itself (same pattern
    `lib.crew.draft.create_wp_draft` uses for the drafting pipeline). Same
    call regardless of `source` -- the worker treats 5 distinct OpenArt
    images and the hero image repeated 5 times identically. Returns True on
    subprocess success.

    `BRAND_DIR` is passed explicitly (not just inherited from `os.environ`)
    as `brand_dir`'s already-resolved absolute path -- confirmed live: the
    worker's own cwd is `_RECIPE_PUBLISHER_ROOT`, so a relative `BRAND_DIR`
    value resolves against *that* directory instead of the engine root,
    silently writing (and DB-recording) the composed mp4s at the wrong
    path entirely. Passing the resolved absolute value here removes the
    ambiguity regardless of what shape `BRAND_DIR` had in this process's
    own environment."""
    with tempfile.TemporaryDirectory() as tmp:
        image_paths = []
        for index, image_bytes in enumerate(images):
            image_path = Path(tmp) / f"beat_{index}.jpg"
            image_path.write_bytes(image_bytes)
            image_paths.append(str(image_path))
        images_path = Path(tmp) / "images.json"
        images_path.write_text(json.dumps(image_paths))
        plan_path = Path(tmp) / "plan.json"
        plan_path.write_text(plan.model_dump_json())

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "workers.worker_wp_ideas_reel",
                    "--idea-id",
                    idea_id,
                    "--images",
                    str(images_path),
                    "--plan",
                    str(plan_path),
                    "--slug",
                    idea_id,
                    "--source",
                    source,
                ],
                cwd=_RECIPE_PUBLISHER_ROOT,
                env={**os.environ, "BRAND_DIR": str(brand_dir)},
                capture_output=True,
                text=True,
                timeout=600,
            )
        except Exception as exc:  # includes subprocess.TimeoutExpired
            logger.error("reels_compose_subprocess_error", idea_id=idea_id, error=str(exc))
            return False

    if result.returncode != 0:
        logger.error(
            "reels_compose_subprocess_failed",
            idea_id=idea_id,
            returncode=result.returncode,
            stderr=(result.stderr or "")[-2000:],
        )
        return False
    return True


def _revert_claim(idea_id: str) -> None:
    """Un-stick a failed composition so it's retried next run rather than
    stuck at 'composing_reel' forever -- same revert-on-failure pattern
    `worker_wp_ideas.py::_revert_if_still_drafting` uses."""
    current = ideas_db.get_idea(idea_id)
    if current is not None and current.get("status") == "composing_reel":
        ideas_db.update_status(idea_id, "wp_published")


def _process_idea(row: dict[str, Any], *, dry_run: bool, brand_dir: Path) -> str:
    idea_id = str(row["id"])
    wp_post_id = row.get("wp_post_id")
    if not wp_post_id:
        return "skipped_no_wp_post_id"

    post = _fetch_wp_post(str(wp_post_id))
    if post is None:
        logger.warning("reels_wp_post_fetch_failed", idea_id=idea_id, wp_post_id=wp_post_id)
        return "error"

    title = post.get("title", {}).get("rendered", "")
    body = _strip_html(post.get("content", {}).get("rendered", ""))[:_BODY_TRUNCATE_CHARS]
    featured_media_id = int(post.get("featured_media") or 0)

    brand_voice = brand_voice_summary(brand_dir)

    agent = build_reels_agent()
    description = build_reels_task_description(title=title, body=body, brand_voice=brand_voice)
    task = build_reels_task(agent, description)
    plan = execute_reels_crew(agent, task)
    if plan is None:
        logger.warning("reels_plan_generation_failed", idea_id=idea_id)
        return "error"

    if dry_run:
        logger.info("reels_dry_run_plan", idea_id=idea_id, plan=plan)
        return "dry_run"

    # Fetched once, up front: needed either way -- as OpenArt's mascot-
    # consistency reference (see `_try_openart_images`) or, if that path
    # fails, reused directly as all 5 fallback images. No reel is possible
    # without it, so a missing hero image is a hard failure before any
    # claim is taken.
    hero_bytes = _fetch_featured_image_bytes(featured_media_id)
    if hero_bytes is None:
        logger.warning("reels_no_hero_image", idea_id=idea_id)
        return "error"

    if not ideas_db.claim_idea_for_reel_composition(idea_id):
        return "skipped_claim_lost"

    images = _try_openart_images(plan, hero_bytes)
    source = "openart"
    if images is None:
        images = [hero_bytes] * len(plan.beats)
        source = "fallback"

    if _compose_reel(idea_id=idea_id, plan=plan, images=images, source=source, brand_dir=brand_dir):
        return f"composed_{source}"

    _revert_claim(idea_id)
    return "error"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--brand-dir", type=Path, default=None)
    p.add_argument("--idea-id", type=str, default=None)
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    brand_dir = (args.brand_dir or _infer_brand_dir()).resolve()
    load_brand_env_into_environ(brand_dir)
    load_local_env()

    missing = _check_required_env()
    if missing:
        print(f"ERROR: missing required env var(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    brand_id = brand_dir.name
    flipped = _detect_publish_sweep(brand_id=brand_id)
    print(f"detect-publish sweep: {flipped} idea(s) marked wp_published")

    if args.idea_id:
        rows = [r for r in ideas_db.list_ideas(brand_id=brand_id, limit=5000) if str(r["id"]) == args.idea_id]
    else:
        rows = ideas_db.list_ideas(status="wp_published", brand_id=brand_id, limit=args.limit)
        rows = [r for r in rows if r.get("reel_ig_video_path") is None]

    if not rows:
        print("no eligible ideas for reel composition")
        return 0

    results: dict[str, int] = {}
    for row in rows:
        outcome = _process_idea(row, dry_run=args.dry_run, brand_dir=brand_dir)
        results[outcome] = results.get(outcome, 0) + 1

    print(f"summary: {json.dumps(results)}")
    return 0 if results.get("error", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
