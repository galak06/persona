"""Shared WordPress-source helpers for the post-derived social pipelines.

Both `scripts/crewai_reels_pipeline.py` (blog post -> IG/FB Reels) and
`scripts/crewai_fb_posts_pipeline.py` (blog post -> FB Page post) start from
the same place: a `content_ideas` row that points at a live WP post. They
need the same four things -- notice the post went live, fetch it, strip its
HTML down to prompt-able text, and pull its hero image -- so that logic lives
here once instead of being copy-pasted into the second pipeline.

This is deliberately a NARROWER kind of sharing than the crew packages under
`lib/crew/<name>/` practise. Those keep their `_json_output_instructions` /
`DEEPSEEK_MODEL` / JSON-extractor copies independent on purpose (see
`lib.crew.categorizer.agent._json_output_instructions`'s docstring, and
`lib.crew.writer.agent`'s on the model constant) because they encode
*per-pipeline editorial choices* that should be free to diverge. Nothing
here is an editorial choice -- it's the WordPress REST API, which is the
same API for every consumer. Sharing it couples the two pipelines to WP's
contract, not to each other's judgement.

Extracted verbatim from `crewai_reels_pipeline.py`; no behaviour change. The
sweep's log event name is a parameter precisely so the reels pipeline keeps
emitting `reels_marked_wp_published` and existing Loki/Grafana queries on
that event keep matching.
"""

from __future__ import annotations

import html
import re
from typing import Any

import httpx

from lib import ideas_db
from lib.observability import get_logger
from lib.sessions.wp_client import wp_client

logger = get_logger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_html(raw_html: str) -> str:
    """Flatten rendered WP HTML to a single run of prompt-able plain text."""
    text = _HTML_TAG_RE.sub(" ", raw_html)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def fetch_post(wp_post_id: str) -> dict[str, Any] | None:
    """Fetch one WP post by id, or None if it isn't retrievable."""
    with wp_client() as client:
        resp = client.get(f"/wp-json/wp/v2/posts/{wp_post_id}")
        if resp.status_code != 200:
            return None
        data: dict[str, Any] = resp.json()
        return data


def fetch_featured_image_bytes(featured_media_id: int) -> bytes | None:
    """Download a post's featured image. None if the post has no featured
    media, the media record is unreadable, or it carries no `source_url`."""
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


def detect_publish_sweep(*, brand_id: str, event: str = "idea_marked_wp_published") -> int:
    """For `wp_draft` rows with `wp_post_id` set, check live WP status and
    flip to `wp_published` if the post is now live. Returns the count flipped.

    Requiring `wp_post_id` scopes this to CrewAI-authored posts only. Always
    safe to run -- a cheap, idempotent status check -- which is why both
    pipelines call it unconditionally, even under `--dry-run`.
    """
    rows = ideas_db.list_ideas(status="wp_draft", brand_id=brand_id, limit=500)
    flipped = 0
    for row in rows:
        wp_post_id = row.get("wp_post_id")
        if not wp_post_id:
            continue
        post = fetch_post(str(wp_post_id))
        if post is None or post.get("status") != "publish":
            continue
        if ideas_db.mark_wp_published(str(row["id"])):
            logger.info(event, idea_id=row["id"])
            flipped += 1
    return flipped
