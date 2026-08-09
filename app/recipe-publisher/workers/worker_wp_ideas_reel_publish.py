"""Reel publisher: posts an already-composed + approved reel to IG and FB.

Invoked once per approval click (`--idea-id`, single row), not on a sweep --
`api/ideas_api.py`'s `PATCH /ideas/{id}/status` handler subprocess-invokes
this the same way `_draft_idea_with_crewai_background` invokes the drafting
pipeline. Works identically regardless of which path composed the reel
(`reel_source='openart'` or `'fallback'`) -- by this point there's just two
video paths + two captions on the row.

Per-platform-independent: if IG succeeds but FB fails, only FB needs a
retry. Re-clicking Approve on the review page re-invokes this same script,
which skips whichever platform already has a URL rather than re-publishing
it -- see `_already_published`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Add the repo root to sys.path so lib/ is importable, and this directory's
# parent to sys.path so `publishers.*` resolves -- same pattern
# worker_wp_ideas_reel.py already uses for `generators.*`.
_RECIPE_PUBLISHER_ROOT = Path(__file__).resolve().parents[1]
_ROOT = _RECIPE_PUBLISHER_ROOT.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_RECIPE_PUBLISHER_ROOT))

from publishers.facebook import publish_reel_to_facebook
from publishers.instagram import publish_reel_to_instagram

from lib import ideas_db

log = logging.getLogger(__name__)


def _resolve_brand_path(relative_path: str) -> Path:
    """`content_ideas.reel_*_video_path` is stored relative to `BRAND_DIR`
    (see `worker_wp_ideas_reel.py`) -- resolved here against *this
    process's own* `BRAND_DIR`, same reasoning as
    `api/ideas_api.py::_resolve_brand_path`: this worker can run in a
    different environment (host vs. the API container that spawns it) than
    whichever process originally composed the file."""
    brand = os.environ.get("BRAND_DIR")
    base = Path(brand) if brand else _ROOT
    return base / relative_path


@dataclass
class _ReelStub:
    """Duck-typed stand-in for `generators.recipe.Recipe` -- the real
    dataclass has a dozen recipe-specific required fields (title,
    ingredients, steps, ...) that don't apply to a WP-post-sourced reel.
    `publish_reel_to_instagram`/`publish_reel_to_facebook` only ever touch
    `.slug` and `.ig_caption` on their `recipe` argument, so this is all
    that's actually needed at runtime."""

    slug: str
    ig_caption: str


def _publish_ig(idea: dict) -> str:
    video_path = _resolve_brand_path(idea["reel_ig_video_path"])
    stub = _ReelStub(slug=str(idea["id"]), ig_caption=idea["reel_ig_caption"])
    result = publish_reel_to_instagram(stub, video_path)
    # A successful call with no permalink still means the post went live
    # (media_id came back) -- record "" rather than None, matching
    # publish_prepared.py's established `permalink or ""` convention. A
    # None here would leave the DB column NULL, and the next Approve click
    # would republish (duplicate post) instead of recognizing this platform
    # as already done.
    return result.permalink or ""


def _publish_fb(idea: dict) -> str:
    video_path = _resolve_brand_path(idea["reel_fb_video_path"])
    stub = _ReelStub(slug=str(idea["id"]), ig_caption=idea["reel_fb_caption"])
    result = publish_reel_to_facebook(stub, video_path, description=idea["reel_fb_caption"])
    return result.permalink or ""


def _delete_if_exists(path_str: str | None) -> None:
    if not path_str:
        return
    path = _resolve_brand_path(path_str)
    if path.exists():
        path.unlink()


def _do_one(idea_id: str) -> str:
    idea = ideas_db.get_idea(idea_id)
    if idea is None or idea.get("status") != "social_queued":
        log.info(json.dumps({"event": "reel_publish_skipped_not_queued", "idea_id": idea_id}))
        return "skipped"

    # `is None` specifically, not falsy -- a previously-recorded successful
    # publish with an empty-string permalink (see `_publish_ig`/`_publish_fb`)
    # must still be recognized as "already done", not retried.
    if idea.get("ig_reel_url") is None:
        try:
            ig_url = _publish_ig(idea)
            ideas_db.set_reel_result(idea_id, ig_reel_url=ig_url)
            _delete_if_exists(idea.get("reel_ig_video_path"))
            log.info(json.dumps({"event": "reel_published_ig", "idea_id": idea_id, "url": ig_url}))
        except Exception as exc:
            log.error(json.dumps({"event": "reel_publish_ig_failed", "idea_id": idea_id, "error": str(exc)}))

    if idea.get("fb_reel_url") is None:
        try:
            fb_url = _publish_fb(idea)
            ideas_db.set_reel_result(idea_id, fb_reel_url=fb_url)
            _delete_if_exists(idea.get("reel_fb_video_path"))
            log.info(json.dumps({"event": "reel_published_fb", "idea_id": idea_id, "url": fb_url}))
        except Exception as exc:
            log.error(json.dumps({"event": "reel_publish_fb_failed", "idea_id": idea_id, "error": str(exc)}))

    current = ideas_db.get_idea(idea_id)
    if current and current.get("status") == "social_done":
        return "published"
    return "partial"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Publish an approved reel to IG and FB")
    p.add_argument("--idea-id", required=True)
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='{"time":"%(asctime)s","level":"%(levelname)s","msg":%(message)s}',
    )

    outcome = _do_one(args.idea_id)
    log.info(json.dumps({"summary": outcome, "idea_id": args.idea_id}))
    return 0 if outcome in ("published", "skipped") else 1


if __name__ == "__main__":
    sys.exit(main())
