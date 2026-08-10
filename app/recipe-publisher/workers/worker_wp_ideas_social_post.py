"""Social-post publisher: posts an approved (caption + hook image) pair.

The publish half of the social-post track (`lib.social_post_db`), invoked
once per row per platform -- never a sweep:

Both callers are `scripts/crewai_social_posts_pipeline.py`'s release sweep,
which only hands over rows whose due-time has passed:

- `--platform fb`: once the approved post's scheduled slot arrives. Publishes
  the Facebook Page photo post, records the result, and arms the IG half's
  `social_post_ig_due_at` (the documented FB<->IG gap).
- `--platform ig`: once that gap has elapsed. Uploads the same image to the WP
  media library (Meta needs a public URL for IG container creation), publishes
  the IG feed post, and moves the row to its terminal 'published' state. The
  local image file is only deleted here, after BOTH platforms are done.

Unlike the reels publish worker's per-platform-independent single pass, the
two platforms here are deliberately NOT published together -- the split *is*
the feature (config.json instagram.min_gap_between_feed_posts_hours; the
"Publishing Coordination" gaps in app/CLAUDE.md).

This is also the first FB/IG *page-post* path that actually enforces the
declared rate limits (`lib.rate_limiter.can_act` -- facebook:page_post 3/d,
instagram:feed_post 2/d; every earlier page-post path skipped the check) and
that records into `lib.engagements_db` + `$BRAND_DIR/state/
publishing_timeline.json`, so `/published` and the timing gaps see these
posts.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Repo root for lib/, this tree's root for publishers.* -- same pattern as
# worker_wp_ideas_reel_publish.py.
_RECIPE_PUBLISHER_ROOT = Path(__file__).resolve().parents[1]
_ROOT = _RECIPE_PUBLISHER_ROOT.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_RECIPE_PUBLISHER_ROOT))

from publishers.facebook import publish_photo_post_to_facebook
from publishers.instagram import publish_to_instagram

from lib import rate_limiter, social_post_db
from lib.engagements_db import record_publish

log = logging.getLogger(__name__)

# Matches config.json instagram.min_gap_between_feed_posts_hours and the
# documented FB<->IG gap ("Publishing Coordination", app/CLAUDE.md).
_IG_GAP_HOURS = 4.0


def _brand_dir() -> Path:
    brand = os.environ.get("BRAND_DIR")
    return Path(brand) if brand else _ROOT


def _resolve_brand_path(relative_path: str) -> Path:
    """`social_post_image_path` is stored relative to `BRAND_DIR` -- resolved
    against *this process's own* `BRAND_DIR`, same reasoning as the reels
    publish worker: composer and publisher may run in different environments
    (host vs API container) sharing one bind-mounted tree."""
    return _brand_dir() / relative_path


@dataclass
class _PostStub:
    """Duck-typed stand-in for `generators.recipe.Recipe`;
    `publish_to_instagram` only touches `.ig_caption` (container caption)
    and `.slug` -- same trick as the reels worker's `_ReelStub`."""

    slug: str
    ig_caption: str


def _touch_timeline(key: str) -> None:
    """Best-effort update of `$BRAND_DIR/state/publishing_timeline.json` --
    the file every skill is documented to consult for cross-platform gaps."""
    try:
        path = _brand_dir() / "state" / "publishing_timeline.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        data[key] = datetime.now(UTC).isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
    except Exception as exc:
        log.warning(json.dumps({"event": "timeline_update_failed", "key": key, "error": str(exc)}))


def _upload_image_for_ig(idea: dict) -> str:
    """IG container creation needs a PUBLIC image URL; the WP media library
    provides one -- exactly what `publish_carousel_to_instagram` does for its
    slides, via the same `lib.crew.wp_media.upload_wp_media` helper."""
    from lib.crew.wp_image import GeneratedImage
    from lib.crew.wp_media import upload_wp_media
    from lib.sessions.wp_client import wp_client

    image_path = _resolve_brand_path(idea["social_post_image_path"])
    image = GeneratedImage(
        url="",
        alt_text=idea.get("social_post_image_alt") or "",
        provider=idea.get("social_post_source") or "gemini",
        bytes_=image_path.read_bytes(),
        content_type="image/jpeg",
    )
    with wp_client() as client:
        _media_id, source_url = upload_wp_media(client, image, f"social-post-{idea['id']}")
    return source_url


def _publish_fb(idea: dict) -> str:
    idea_id = str(idea["id"])
    result = publish_photo_post_to_facebook(
        image_path=_resolve_brand_path(idea["social_post_image_path"]),
        message=idea["social_post_fb_caption"],
        alt_text=idea.get("social_post_image_alt") or "",
    )
    permalink = result.permalink or ""
    social_post_db.set_fb_result(idea_id, url=permalink, ig_gap_hours=_IG_GAP_HOURS)
    rate_limiter.record_action("facebook", "page_post")
    record_publish(
        platform="facebook",
        kind="page_post",
        permalink=permalink,
        content=idea["social_post_fb_caption"],
        source_ref=idea_id,
        ref=result.post_id,
    )
    _touch_timeline("last_fb_page_post")
    return permalink


def _publish_ig(idea: dict) -> str:
    idea_id = str(idea["id"])
    image_url = _upload_image_for_ig(idea)
    stub = _PostStub(slug=idea_id, ig_caption=idea["social_post_ig_caption"])
    result = publish_to_instagram(stub, image_url=image_url)
    permalink = result.permalink or ""
    social_post_db.set_ig_result(idea_id, url=permalink)
    rate_limiter.record_action("instagram", "feed_post")
    record_publish(
        platform="instagram",
        kind="feed_post",
        permalink=permalink,
        content=idea["social_post_ig_caption"],
        source_ref=idea_id,
        ref=result.media_id,
    )
    _touch_timeline("last_ig_feed_post")
    # Both platforms are done -- only now is the local file disposable.
    image_path = _resolve_brand_path(idea["social_post_image_path"])
    if image_path.exists():
        image_path.unlink()
    return permalink


def _do_one(idea_id: str, platform: str) -> str:
    from lib import ideas_db  # row fetch only; state transitions live in social_post_db

    idea = ideas_db.get_idea(idea_id)
    if idea is None:
        log.error(json.dumps({"event": "social_post_publish_no_idea", "idea_id": idea_id}))
        return "error"

    expected_status = "scheduled" if platform == "fb" else "fb_published"
    if idea.get("social_post_status") != expected_status:
        log.info(
            json.dumps(
                {
                    "event": "social_post_publish_skipped_wrong_status",
                    "idea_id": idea_id,
                    "platform": platform,
                    "status": idea.get("social_post_status"),
                }
            )
        )
        return "skipped"

    # Second line of defence on timing. The release sweep only hands over rows
    # whose slot has arrived, but this worker is directly invocable, and a
    # post firing early is exactly what the spread exists to prevent.
    due_column = "social_post_fb_due_at" if platform == "fb" else "social_post_ig_due_at"
    due_at = idea.get(due_column)
    if due_at is not None and due_at > datetime.now(UTC):
        log.info(
            json.dumps(
                {
                    "event": "social_post_publish_skipped_not_due",
                    "idea_id": idea_id,
                    "platform": platform,
                    "due_at": str(due_at),
                }
            )
        )
        return "skipped"

    rl_platform, rl_action = (
        ("facebook", "page_post") if platform == "fb" else ("instagram", "feed_post")
    )
    if not rate_limiter.can_act(rl_platform, rl_action):
        log.error(
            json.dumps(
                {
                    "event": "social_post_publish_rate_limited",
                    "idea_id": idea_id,
                    "platform": platform,
                }
            )
        )
        return "rate_limited"

    try:
        url = _publish_fb(idea) if platform == "fb" else _publish_ig(idea)
    except Exception as exc:
        log.error(
            json.dumps(
                {
                    "event": f"social_post_publish_{platform}_failed",
                    "idea_id": idea_id,
                    "error": str(exc),
                }
            )
        )
        return "error"

    log.info(
        json.dumps(
            {
                "event": f"social_post_published_{platform}",
                "idea_id": idea_id,
                "url": url,
            }
        )
    )
    return "published"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Publish an approved social post (one platform)")
    p.add_argument("--idea-id", required=True)
    p.add_argument("--platform", required=True, choices=("fb", "ig"))
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='{"time":"%(asctime)s","level":"%(levelname)s","msg":%(message)s}',
    )

    outcome = _do_one(args.idea_id, args.platform)
    log.info(json.dumps({"summary": outcome, "idea_id": args.idea_id, "platform": args.platform}))
    return 0 if outcome in ("published", "skipped") else 1


if __name__ == "__main__":
    sys.exit(main())
