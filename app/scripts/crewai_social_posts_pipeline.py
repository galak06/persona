"""WP-post -> FB Page post + IG feed post orchestrator (composition side).

Three phases, run together on every invocation:

1. **Detect-publish sweep** (`lib.crew.wp_source.detect_publish_sweep`) --
   flips CrewAI-authored `wp_draft` rows to `wp_published` once the post is
   confirmed live. Always real, even under `--dry-run`, same as the reels
   pipeline it mirrors.
2. **Composition** -- for postable ideas with no social-post track started
   (`lib.social_post_db.list_candidates`; independent of the reels track, so
   an idea whose reel is already done is still eligible), claims the row,
   runs the Social Post Writer crew (`lib.crew.socialpost`) for both platform
   captions + the shared image direction, generates ONE hook image
   (`lib.crew.wp_image.generate_wp_image` conditioned on the WP post's own
   featured image), paints the overlay + CTA ribbon + follow badge on it
   (`recipe-publisher/generators/text_overlay`), scans both captions with
   `lib.medical_claims_validator.find_banned_claims` (flagged, not blocking
   -- the human gate sees the flags), and lands the row at
   `social_post_status='queued'` for review on the frontend. Publishing only
   happens after approval (`worker_wp_ideas_social_post.py`).
3. **Release sweep** -- approval SCHEDULES rather than publishes (each
   approved post takes the next free daily slot, see `lib.social_post_slots`),
   so this phase is what actually posts: FB rows whose slot has arrived, then
   IG halves whose FB<->IG gap has elapsed. Runs on every invocation, so any
   run flushes whatever became due; `--release-only` runs just this, which is
   the mode the hourly `social-posts-release` cron flow runs. Skipped under
   `--dry-run` and under `--compose-only`.

The two cron flows are deliberately disjoint modes of this one script:
`social-posts-compose` (daily, `--compose-only`) fills the review queue and
never publishes, `social-posts-release` (hourly, `--release-only`) publishes
what review approved and never composes. Publishing stays owned by exactly
one flow because `worker_wp_ideas_social_post`'s status guard is a
read-then-check, not an atomic claim -- two release sweeps overlapping on the
same due row could both post it.

Image fallback: if Gemini generation fails for any reason, the WP post's own
hero image is used instead (`social_post_source='fallback'`), overlays still
applied -- same all-or-nothing philosophy as the reels pipeline's OpenArt
fallback.

Usage::

    python -m scripts.crewai_social_posts_pipeline --dry-run
    python -m scripts.crewai_social_posts_pipeline
    python -m scripts.crewai_social_posts_pipeline --idea-id <content_ideas id>
    python -m scripts.crewai_social_posts_pipeline --compose-only
    python -m scripts.crewai_social_posts_pipeline --release-only
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from lib import social_post_db
from lib.crew import wp_source
from lib.crew.context import brand_voice_summary
from lib.crew.socialpost import (
    build_social_post_agent,
    build_social_post_task,
    execute_social_post_crew,
)
from lib.crew.socialpost.compose import compose_image, generate_hook_image
from lib.crew.socialpost.prompts import build_social_post_task_description
from lib.local_env import load_brand_env_into_environ, load_local_env
from lib.medical_claims_validator import find_banned_claims
from lib.observability import get_logger

logger = get_logger(__name__)

# Publishing needs the WP credentials (the IG half uploads the image to the WP
# media library, because Meta requires a public URL to build a container) and
# nothing else -- the publishers read FB_PAGE_ID/FB_PAGE_TOKEN/IG_ACCOUNT_ID
# themselves and raise their own clear errors.
_RELEASE_ENV_VARS = ("WP_URL", "WP_USER", "WP_APP_PASSWORD")
# Composition additionally needs the writer LLM and the image model.
_COMPOSE_ENV_VARS = ("DEEPSEEK_API_KEY", "GEMINI_API_KEY", *_RELEASE_ENV_VARS)
_BODY_TRUNCATE_CHARS = 3000
_RECIPE_PUBLISHER_ROOT = _ENGINE_ROOT / "recipe-publisher"


def _infer_brand_dir() -> Path:
    """Same convention as `scripts/crewai_reels_pipeline.py::_infer_brand_dir`."""
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


def _check_required_env(*, release_only: bool) -> list[str]:
    """Missing env vars for the mode actually being run.

    `--release-only` deliberately checks a SMALLER set: it never builds a
    caption or an image, so demanding DEEPSEEK_API_KEY/GEMINI_API_KEY there is
    wrong. It also breaks the scheduled path for real -- the cron flow runs
    release-only inside the worker container, which has no reason to carry LLM
    credentials, and the run died on `missing required env var(s):
    DEEPSEEK_API_KEY` with four posts sitting past their slot.
    """
    required = _RELEASE_ENV_VARS if release_only else _COMPOSE_ENV_VARS
    return [name for name in required if not os.environ.get(name, "").strip()]


def _site_identity(brand_dir: Path) -> tuple[str, str]:
    """(site_domain, mascot_name) from the brand config, with safe fallbacks."""
    try:
        config = json.loads((brand_dir / "config.json").read_text())
        site = config.get("site", {})
        url = str(site.get("url", "")).rstrip("/")
        domain = url.split("//", 1)[-1] if url else brand_dir.name
        return domain, str(site.get("mascot_name", ""))
    except Exception as exc:
        logger.warning("social_posts_config_read_failed", error=str(exc))
        return brand_dir.name, ""


def _process_idea(row: dict[str, Any], *, dry_run: bool, brand_dir: Path) -> str:
    idea_id = str(row["id"])
    wp_post_id = row.get("wp_post_id")
    wp_url = str(row.get("wp_url") or "")
    if not wp_post_id or not wp_url:
        return "skipped_no_wp_post"

    post = wp_source.fetch_post(str(wp_post_id))
    if post is None:
        logger.warning("social_posts_wp_fetch_failed", idea_id=idea_id, wp_post_id=wp_post_id)
        return "error"

    title = post.get("title", {}).get("rendered", "")
    body = wp_source.strip_html(post.get("content", {}).get("rendered", ""))[:_BODY_TRUNCATE_CHARS]
    featured_media_id = int(post.get("featured_media") or 0)

    site_domain, mascot_name = _site_identity(brand_dir)
    target_keyword = str(row.get("target_keyword") or "")

    agent = build_social_post_agent()
    description = build_social_post_task_description(
        title=title,
        body=body,
        target_keyword=target_keyword,
        site_domain=site_domain,
        brand_voice=brand_voice_summary(brand_dir),
    )
    task = build_social_post_task(agent, description)
    plan = execute_social_post_crew(agent, task, target_keyword=target_keyword)
    if plan is None:
        logger.warning("social_posts_plan_generation_failed", idea_id=idea_id)
        return "error"

    if dry_run:
        logger.info("social_posts_dry_run_plan", idea_id=idea_id, plan=plan)
        return "dry_run"

    # Needed either way -- as the fallback image if Gemini fails, and a post
    # with no image at all is not worth queueing. Hard failure before claiming.
    hero_bytes = wp_source.fetch_featured_image_bytes(featured_media_id)
    if hero_bytes is None:
        logger.warning("social_posts_no_hero_image", idea_id=idea_id)
        return "error"

    if not social_post_db.claim(idea_id):
        return "skipped_claim_lost"

    # The WP hero doubles as the generation reference (mascot consistency +
    # grounding in this post's real subject) and the fallback image.
    image_bytes, source = generate_hook_image(plan, mascot_name=mascot_name, hero_bytes=hero_bytes)
    # Same convention as worker_post_stories.py: IG_USERNAME env, brand-folder
    # name as the fallback (for this brand that IS the IG handle).
    ig_handle = f"@{os.environ.get('IG_USERNAME', brand_dir.name)}"
    image_path = compose_image(
        plan, idea_id=idea_id, image_bytes=image_bytes, brand_dir=brand_dir, ig_handle=ig_handle
    )
    if image_path is None:
        social_post_db.revert_claim(idea_id)
        return "error"

    flags = find_banned_claims(f"{plan.fb_caption}\n{plan.ig_caption}")
    if not social_post_db.set_pending_review(
        idea_id,
        fb_caption=plan.fb_caption,
        ig_caption=plan.ig_caption,
        image_path=image_path,
        image_alt=plan.image_alt_text,
        source=source,
        validation_flags=flags,
    ):
        social_post_db.revert_claim(idea_id)
        return "error"
    return f"composed_{source}"


def _publish_one(idea_id: str, platform: str, *, brand_dir: Path) -> bool:
    """Subprocess-invoke the publish worker for one row on one platform."""
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "workers.worker_wp_ideas_social_post",
                "--idea-id",
                idea_id,
                "--platform",
                platform,
            ],
            cwd=_RECIPE_PUBLISHER_ROOT,
            env={**os.environ, "BRAND_DIR": str(brand_dir)},
            capture_output=True,
            text=True,
            timeout=600,
        )
    except Exception as exc:  # includes subprocess.TimeoutExpired
        logger.error(
            "social_posts_release_error", idea_id=idea_id, platform=platform, error=str(exc)
        )
        return False
    if result.returncode != 0:
        logger.error(
            "social_posts_release_failed",
            idea_id=idea_id,
            platform=platform,
            returncode=result.returncode,
            stderr=(result.stderr or "")[-2000:],
        )
        return False
    return True


def _release_due(*, brand_id: str, brand_dir: Path) -> dict[str, int]:
    """Phase 3: publish everything whose time has come -- FB posts whose
    scheduled slot has arrived, then IG halves whose FB<->IG gap has elapsed.

    This is what turns approval-as-scheduling into actual posts, so it runs on
    EVERY invocation (not just `--release-only`): any run of this pipeline
    flushes whatever became due since the last one.

    FB first, deliberately: a row that publishes to FB in this pass arms its
    IG due-time as a side effect, and that time is always in the future, so it
    can't also fire in the same pass. No double-post, no ordering surprise.
    """
    outcomes: dict[str, int] = {}
    for row in social_post_db.list_due_for_fb(brand_id=brand_id):
        key = "fb_published" if _publish_one(str(row["id"]), "fb", brand_dir=brand_dir) else "error"
        outcomes[key] = outcomes.get(key, 0) + 1
    for row in social_post_db.list_due_for_ig(brand_id=brand_id):
        key = "ig_published" if _publish_one(str(row["id"]), "ig", brand_dir=brand_dir) else "error"
        outcomes[key] = outcomes.get(key, 0) + 1
    return outcomes


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--brand-dir", type=Path, default=None)
    p.add_argument("--idea-id", type=str, default=None)
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--dry-run", action="store_true")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--release-only",
        action="store_true",
        help="skip composition entirely; only publish what is already due "
        "(this is the mode the hourly release cron runs)",
    )
    mode.add_argument(
        "--compose-only",
        action="store_true",
        help="skip the release sweep entirely; only compose new posts into the "
        "review queue (this is the mode the daily compose cron runs, so that "
        "publishing stays owned by the hourly release flow alone)",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    brand_dir = (args.brand_dir or _infer_brand_dir()).resolve()
    load_brand_env_into_environ(brand_dir)
    load_local_env()

    missing = _check_required_env(release_only=args.release_only)
    if missing:
        print(f"ERROR: missing required env var(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    brand_id = brand_dir.name
    results: dict[str, int] = {}

    if not args.release_only:
        flipped = wp_source.detect_publish_sweep(
            brand_id=brand_id, event="social_posts_marked_wp_published"
        )
        print(f"detect-publish sweep: {flipped} idea(s) marked wp_published")

        if args.idea_id:
            row = next(
                (
                    r
                    for r in social_post_db.list_candidates(brand_id=brand_id, limit=500)
                    if str(r["id"]) == args.idea_id
                ),
                None,
            )
            rows = [row] if row else []
            if not rows:
                print(f"idea {args.idea_id} is not a social-post candidate")
        else:
            rows = social_post_db.list_candidates(brand_id=brand_id, limit=args.limit)

        for row in rows:
            outcome = _process_idea(row, dry_run=args.dry_run, brand_dir=brand_dir)
            results[outcome] = results.get(outcome, 0) + 1

    if not args.dry_run and not args.compose_only:
        for key, count in _release_due(brand_id=brand_id, brand_dir=brand_dir).items():
            results[key] = results.get(key, 0) + count

    print(f"summary: {json.dumps(results)}")
    return 0 if results.get("error", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
