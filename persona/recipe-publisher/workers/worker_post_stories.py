"""Worker — post_stories.

Publishes an Instagram Story from the hero image of the most recently
social-published recipe, via the Instagram Graph API.

Flow:
    1. Generate branded story card (story_card.py)
    2. Upload to WordPress media library → public URL
    3. Create IG media container (media_type=STORIES)
    4. Publish container
    5. Send Telegram reminder to add link sticker on phone

    python -m workers.worker_post_stories                    # dry-run plan
    python -m workers.worker_post_stories --apply --limit 1  # post one story
    python -m workers.worker_post_stories --health-check     # env check → 0/1
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository
from workers._base import run_worker

logger = logging.getLogger("workers.post_stories")

_SA_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_SA_ROOT / "lib"))
from bootstrap import init_script as _init_script  # noqa: E402
import notifier as _notifier  # noqa: E402
_settings, _ = _init_script(__name__)

_BADGE_PATH = str(_SA_ROOT.parent / "persona/images/badge.png")
_IG_GRAPH = "https://graph.facebook.com/v23.0"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _upload_to_wp_media(jpeg_bytes: bytes, filename: str) -> str:
    """Upload JPEG to WordPress media library. Returns the public source_url."""
    wp_base = os.environ["WP_URL"].rstrip("/")
    r = httpx.post(
        f"{wp_base}/wp-json/wp/v2/media",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "image/jpeg",
        },
        content=jpeg_bytes,
        auth=(os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"]),
        timeout=30.0,
    )
    r.raise_for_status()
    url = r.json()["source_url"]
    logger.info("uploaded story card to WP media → %s", url)
    return url


def _publish_ig_story(image_url: str) -> str:
    """Create and publish an IG Story via Graph API. Returns the stories profile URL."""
    token = os.environ["FB_PAGE_TOKEN"]
    ig_id = os.environ["IG_ACCOUNT_ID"]

    r = httpx.post(
        f"{_IG_GRAPH}/{ig_id}/media",
        params={"image_url": image_url, "media_type": "STORIES", "access_token": token},
        timeout=30.0,
    )
    r.raise_for_status()
    creation_id = r.json()["id"]
    logger.info("IG story container created: %s", creation_id)

    r = httpx.post(
        f"{_IG_GRAPH}/{ig_id}/media_publish",
        params={"creation_id": creation_id, "access_token": token},
        timeout=30.0,
    )
    r.raise_for_status()
    logger.info("IG story published: media_id=%s", r.json()["id"])

    ig_username = os.environ.get("IG_USERNAME", "persona")
    return f"https://www.instagram.com/stories/{ig_username}/"


def _resolve_image_url(row: RecipeRow) -> str:
    if row.hero_image_url:
        return row.hero_image_url
    if not row.wp_url:
        raise ValueError(f"{row.id}: no hero_image_url and no wp_url to fall back to")
    slug = row.wp_url.rstrip("/").rsplit("/", 1)[-1]
    wp_base = os.environ.get("WP_URL", "").rstrip("/")
    r = httpx.get(
        f"{wp_base}/wp-json/wp/v2/posts",
        params={"slug": slug, "_embed": "1"},
        auth=(os.environ.get("WP_USER", ""), os.environ.get("WP_APP_PASSWORD", "")),
        timeout=15.0,
    )
    r.raise_for_status()
    posts = r.json()
    if not posts:
        raise ValueError(f"{row.id}: WP post not found for slug '{slug}'")
    featured = posts[0].get("_embedded", {}).get("wp:featuredmedia", [{}])[0]
    url = featured.get("source_url", "")
    if not url:
        raise ValueError(f"{row.id}: WP post has no featured image")
    logger.info("%s: resolved image from WP featured media → %s", row.id, url)
    return url


def _targets(repo: RecipeRepository, seeds: list[str], limit: int) -> list[RecipeRow]:
    rows = [
        r
        for r in repo.list_recipes()
        if r.social_published_at
        and (r.hero_image_url or r.wp_url)
        and "ig_story" not in r.publish_status
        and (not seeds or r.id in seeds)
    ]
    rows.sort(key=lambda r: r.social_published_at, reverse=True)
    return rows[: limit if limit else 1]


def _do_one(repo: RecipeRepository, row: RecipeRow) -> str:
    from generators.story_card import compose_story_card

    image_url = _resolve_image_url(row)
    resp = httpx.get(image_url, follow_redirects=True, timeout=30.0)
    resp.raise_for_status()
    logger.info("%s: hero image downloaded (%d bytes)", row.id, len(resp.content))

    recipe_name = row.display_name or getattr(row, "name", None) or row.id
    badge_path = _BADGE_PATH if os.path.exists(_BADGE_PATH) else ""
    card_bytes = compose_story_card(resp.content, recipe_name, row.wp_url or "", badge_path=badge_path)

    public_url = _upload_to_wp_media(card_bytes, f"story-{row.id}.jpg")
    story_url = _publish_ig_story(public_url)

    repo.set_publish_status(row.id, {
        **row.publish_status,
        "ig_story": {"state": "published", "url": story_url, "at": _now_iso()},
    })
    logger.info("%s: ig_story published → %s", row.id, story_url)

    _notifier.send(
        f"📸 Story posted! Open Instagram to add the link sticker 🔗\n"
        f"{recipe_name}\n"
        f"{row.wp_url or ''}\n"
        f"{story_url}",
        silent=False,
    )
    return f"ig_story:{story_url}"


def _health() -> bool:
    missing = [
        k for k in ("FB_PAGE_TOKEN", "IG_ACCOUNT_ID", "WP_URL", "WP_USER", "WP_APP_PASSWORD")
        if not os.environ.get(k)
    ]
    if missing:
        logger.error("missing env vars: %s", ", ".join(missing))
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    return run_worker(
        "post_stories",
        targets_fn=_targets,
        do_one_fn=_do_one,
        health_fn=_health,
        argv=argv,
    )


if __name__ == "__main__":
    sys.exit(main())
