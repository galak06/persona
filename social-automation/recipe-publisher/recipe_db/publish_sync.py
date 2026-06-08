"""Sync per-channel publish status onto recipe rows from the publish records.

Sources of truth (read-only):
- ``{brand}/campaigns/**/metadata.json`` — rich per-campaign record with
  ``wp_live_url`` / ``ig_reel_*`` / ``fb_page_post_*`` keyed by seed_id/slug.
- ``recipe-publisher/state/published_recipes.json`` — slim per-slug list.

A recipe matches a record when ``recipe.id`` (its slug) equals the record's
``seed_id`` or ``slug``. The PDF channel mirrors WP — the recipe card is
generated and attached during the WP publish, not recorded separately.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from recipe_db.repository import RecipeRepository

logger = logging.getLogger("recipe_db.publish_sync")

CHANNELS = ("wp", "pdf", "ig", "fb")


def _channel(published: bool, url: str, ref: str, at: str) -> dict[str, str]:
    return {
        "state": "published" if published else "",
        "url": url if published else "",
        "ref": ref if published else "",
        "at": at if published else "",
    }


def build_publish_status(
    record: dict[str, object],
) -> dict[str, dict[str, str]]:
    """Map a raw publish record to the ``{channel: {state,url,ref,at}}`` shape."""

    def _s(key: str) -> str:
        val = record.get(key)
        return str(val) if val not in (None, "") else ""

    at = _s("published_at")
    wp_url, wp_ref = _s("wp_live_url"), _s("wp_post_id")
    wp_pub = bool(wp_url or wp_ref)
    ig_url = _s("ig_reel_permalink")
    ig_ref = _s("ig_reel_media_id") or _s("ig_media_id")
    ig_pub = bool(ig_url or ig_ref)
    fb_url = _s("fb_page_post_permalink") or _s("fb_reel_permalink")
    fb_ref = _s("fb_page_post_id") or _s("fb_reel_post_id")
    fb_pub = bool(fb_url or fb_ref)
    return {
        "wp": _channel(wp_pub, wp_url, wp_ref, at),
        "pdf": _channel(wp_pub, wp_url, wp_ref, at),  # tied to WP recipe card
        "ig": _channel(ig_pub, ig_url, ig_ref, at),
        "fb": _channel(fb_pub, fb_url, fb_ref, at),
    }


def collect_publish_records(
    campaigns_root: Path | None,
    published_recipes_path: Path | None,
) -> dict[str, dict[str, object]]:
    """Gather publish records keyed by seed_id and slug from all sources."""
    records: dict[str, dict[str, object]] = {}

    if campaigns_root and campaigns_root.exists():
        for meta in campaigns_root.rglob("metadata.json"):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            for key in (data.get("seed_id"), data.get("slug")):
                if isinstance(key, str) and key:
                    records.setdefault(key, {}).update(data)

    if published_recipes_path and published_recipes_path.exists():
        try:
            arr = json.loads(
                published_recipes_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            arr = []
        if isinstance(arr, list):
            for entry in arr:
                if not isinstance(entry, dict):
                    continue
                slug = entry.get("slug")
                if not isinstance(slug, str) or not slug:
                    continue
                rec = records.setdefault(slug, {})
                rec.setdefault("wp_post_id", entry.get("wp_post_id"))
                rec.setdefault("ig_media_id", entry.get("ig_media_id"))
                rec.setdefault("published_at", entry.get("published_at"))

    return records


def sync_publish_status(
    repo: RecipeRepository,
    campaigns_root: Path | None,
    published_recipes_path: Path | None,
) -> int:
    """Update publish_status on every matching recipe. Returns rows updated."""
    records = collect_publish_records(campaigns_root, published_recipes_path)
    updated = 0
    for row in repo.list_recipes():
        record = records.get(row.id)
        if record is None:
            continue
        status = build_publish_status(record)
        if status != row.publish_status:
            repo.set_publish_status(row.id, status)
            updated += 1
    logger.info("synced publish status for %d recipe(s)", updated)
    return updated
