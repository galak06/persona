"""Worker A — wp+pdf.

A new dog-safe recipe with no WordPress post gets a live WP draft/post plus a
downloadable recipe-card PDF. The PDF self-heals: a row whose WP already exists
but whose PDF is missing is re-selected for the PDF arm only.

Poll predicate (independent + idempotent — no other worker's state referenced):
    (dog_safe AND no wp_url)  OR  (wp_post_id AND no pdf_url)

On success it fills ``wp_url`` + ``wp_post_id`` (→ Worker B polls on ``wp_url``)
and ``pdf_url``, and records ``artifacts_path`` (the campaign folder seam).

    python -m workers.worker_wp_pdf                   # dry-run plan
    python -m workers.worker_wp_pdf --apply --limit 1 # publish one
    python -m workers.worker_wp_pdf --health-check    # WP reachability → 0/1
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository

from workers._base import run_worker
from workers._folder import (
    artifacts_rel,
    campaign_folder,
    ensure_seed_exported,
    rehydrate_recipe,
)

if TYPE_CHECKING:
    from generators.recipe import Recipe

logger = logging.getLogger("workers.wp_pdf")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _write_publish_inputs(
    folder: Path, recipe: Recipe, wp_id: int, link: str
) -> None:
    """Write the folder files Worker D's publish step (publish_one) consumes:
    ``metadata.json`` + ``ig_caption.txt`` + ``fb_caption.txt``. Worker A owns
    these because it holds the freshly generated Recipe (captions, tags, slug).
    """
    folder.mkdir(parents=True, exist_ok=True)
    fb_caption = getattr(recipe, "fb_caption", "") or ""
    meta = {
        "seed_id": recipe.seed_id,
        "slug": recipe.slug,
        "title": recipe.title,
        "tags": list(recipe.tags),
        "wp_draft_id": wp_id,
        "wp_draft_preview_url": link,
        "ig_caption": recipe.ig_caption,
        "fb_caption": fb_caption,
        "carousel_slides": [],  # publish_one reads slides/ from disk directly
    }
    (folder / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (folder / "ig_caption.txt").write_text(recipe.ig_caption, encoding="utf-8")
    (folder / "fb_caption.txt").write_text(fb_caption, encoding="utf-8")


def _targets(
    repo: RecipeRepository, seeds: list[str], limit: int
) -> list[RecipeRow]:
    """Rows needing WP (dog_safe, no wp_url) OR only the PDF (wp_post_id set,
    pdf_url empty). Each arm is self-gating, so re-running is a no-op."""
    rows = [
        r
        for r in repo.list_recipes()
        if ((r.dog_safe and not r.wp_url) or (r.wp_post_id and not r.pdf_url))
        and (not seeds or r.id in seeds)
    ]
    rows.sort(key=lambda r: r.id)
    return rows[:limit] if limit else rows


def _wp_live_post(slug: str) -> tuple[int, str] | None:
    """Return (post_id, link) if a published WP post already exists for slug."""
    import requests
    from requests.auth import HTTPBasicAuth

    base = os.environ["WP_URL"].rstrip("/")
    auth = HTTPBasicAuth(os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"])
    resp = requests.get(
        f"{base}/wp-json/wp/v2/posts",
        params={"slug": slug, "status": "publish", "_fields": "id,link"},
        auth=auth,
        timeout=25,
    )
    if resp.status_code == 200 and resp.json():
        hit = resp.json()[0]
        return int(hit["id"]), str(hit.get("link", ""))
    return None


def _generate_pdf(wp_id: int) -> str:
    """Generate + upload + inject the recipe-card PDF. Returns its url, or '' when
    recipe cards are disabled or the post has no parseable recipe."""
    from lib.config import settings
    from lib.recipe_card import content_parser, pdf_generator
    from lib.recipe_card import wp_sync as rc_wp

    rc = settings.recipe_card
    if not rc.enabled:
        return ""
    post = rc_wp.fetch_post_data(wp_id)
    recipe = content_parser.parse_recipe(post["title"], post["content"])
    if not (recipe.ingredients or recipe.instructions):
        logger.warning("WP %d: no parseable recipe — skipping PDF", wp_id)
        return ""
    stamp = rc_wp.fetch_nalla_stamp(rc.stamp_media_id)
    pdf = pdf_generator.generate_recipe_card_pdf(
        title=recipe.title,
        ingredients=recipe.ingredients,
        instructions=recipe.instructions,
        nalla_stamp_bytes=stamp,
        cook_temp=recipe.cook_temp,
        cook_time=recipe.cook_time,
        header_title=rc.header_title,
        footer_text=rc.footer_text,
    )
    pdf_url = rc_wp.upload_pdf(pdf, f"recipe-card-{wp_id}.pdf")
    rc_wp.inject_download_button(wp_id, pdf_url)
    return pdf_url


def _record_channel(
    repo: RecipeRepository, recipe_id: str, channel: str, url: str, ref: int
) -> None:
    """Merge one channel into publish_status (preserves the viewer's badges)."""
    fresh = repo.get_recipe(recipe_id)
    status = {ch: dict(v) for ch, v in (fresh.publish_status if fresh else {}).items()}
    status[channel] = {
        "state": "published",
        "url": url,
        "ref": str(ref),
        "at": _now_iso(),
    }
    repo.set_publish_status(recipe_id, status)


def _do_wp(repo: RecipeRepository, row: RecipeRow) -> tuple[int, str]:
    """WP arm: rehydrate → dedup guard → publish → record wp_post_id + wp_url."""
    from generators.image import generate_image
    from publishers.wordpress import publish_to_wordpress

    ensure_seed_exported(row)
    recipe = rehydrate_recipe(row)

    live = _wp_live_post(recipe.slug)
    if live is not None:
        wp_id, link = live
    else:
        image = generate_image(recipe.image_brief, alt_hint=recipe.title)
        wp = publish_to_wordpress(recipe, image)
        wp_id, link = wp.post_id, wp.permalink

    folder = campaign_folder(row)
    _write_publish_inputs(folder, recipe, wp_id, link)
    repo.set_artifacts_path(row.id, artifacts_rel(row))
    repo.set_wp_post_id(row.id, wp_id)
    _record_channel(repo, row.id, "wp", link, wp_id)
    return wp_id, "exists" if live is not None else "wp"


def _do_one(repo: RecipeRepository, row: RecipeRow) -> str:
    """Run the WP arm (if needed) then the PDF arm (if needed)."""
    steps: list[str] = []
    wp_id = row.wp_post_id
    if not row.wp_url:
        wp_id, suffix = _do_wp(repo, row)
        steps.append(suffix)
    if wp_id and not row.pdf_url:
        pdf_url = _generate_pdf(wp_id)
        if pdf_url:
            repo.set_pdf_url(row.id, pdf_url)
            _record_channel(repo, row.id, "pdf", pdf_url, wp_id)
            steps.append("pdf")
        else:
            steps.append("nopdf")
    return "+".join(steps) or "noop"


def _health() -> bool:
    """Required WP env present + WP REST reachable."""
    required = ["WP_URL", "WP_USER", "WP_APP_PASSWORD"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        logger.error("missing env: %s", ", ".join(missing))
        return False
    try:
        import requests
        from requests.auth import HTTPBasicAuth

        base = os.environ["WP_URL"].rstrip("/")
        resp = requests.get(
            f"{base}/wp-json/wp/v2/posts",
            params={"per_page": 1, "_fields": "id"},
            auth=HTTPBasicAuth(os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"]),
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as exc:
        logger.error("WP unreachable: %s", exc)
        return False


def main(argv: list[str] | None = None) -> int:
    return run_worker(
        "wp_pdf",
        targets_fn=_targets,
        do_one_fn=_do_one,
        health_fn=_health,
        argv=argv,
    )


if __name__ == "__main__":
    import sys

    sys.exit(main())
