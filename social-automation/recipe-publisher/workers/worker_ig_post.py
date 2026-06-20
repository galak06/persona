"""Worker IG-post — publish a recipe card to Instagram as a single-image post.

Poll predicate (independent + idempotent):
    row.card_html_created_at truthy  AND  row.ig_url == ""

Fallback when all HTML-ready recipes are already posted: re-post the oldest
HTML-ready recipe (allows controlled repost without manual intervention).

On success it fills ``ig_url`` inside ``publish_status`` and the denormalized
``ig_url`` column, so this worker becomes a no-op on the same recipe.

    python -m workers.worker_ig_post                    # dry-run plan
    python -m workers.worker_ig_post --apply --limit 1  # publish one
    python -m workers.worker_ig_post --health-check     # env probe → 0/1
"""

from __future__ import annotations

import base64
import datetime
import logging
import os
import sys
from pathlib import Path

_rp_root = Path(__file__).resolve().parent.parent
if str(_rp_root) not in sys.path:
    sys.path.insert(0, str(_rp_root))

from publishers.instagram import publish_to_instagram
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository

from workers._base import run_worker
from workers._folder import campaign_folder, rehydrate_recipe

_log = logging.getLogger("workers.ig_post")

# ---------------------------------------------------------------------------
# Category → hashtag map (exactly 6 per category — HARD brand rule)
# ---------------------------------------------------------------------------
_CATEGORY_HASHTAGS: dict[str, list[str]] = {
    "Food & Diet": [
        "#dogfood", "#homemadedogfood", "#dogrecipes",
        "#dognutrition", "#healthydogfood", "#nallasdad",
    ],
    "Grooming": [
        "#doggrooming", "#dogcare", "#homemadedog",
        "#dogdiy", "#petcare", "#nallasdad",
    ],
    "Lifestyle & Gear": [
        "#doglife", "#doglifestyle", "#dogowner",
        "#petlife", "#dogmom", "#nallasdad",
    ],
    "Training": [
        "#dogtraining", "#dogbehavior", "#positivereinforcement",
        "#doglife", "#petcare", "#nallasdad",
    ],
}
_DEFAULT_HASHTAGS = _CATEGORY_HASHTAGS["Food & Diet"]

_ENGAGEMENT_QUESTIONS: dict[str, str] = {
    "Food & Diet": "Does your dog have a favourite homemade treat?",
    "Grooming": "What's your dog's least favourite part of grooming day?",
    "Lifestyle & Gear": "What gear has made the biggest difference for you and your dog?",
    "Training": "What's the one trick your dog picked up faster than you expected?",
}
_DEFAULT_QUESTION = "Have you tried making homemade food for your dog?"


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def _targets(repo: RecipeRepository, seeds: list[str], limit: int) -> list[RecipeRow]:
    """Primary: HTML ready, not yet posted to IG. Fallback: oldest HTML-ready (repost)."""
    rows = [r for r in repo.list_recipes() if r.card_html_created_at and not r.ig_url]
    rows.sort(key=lambda r: r.card_html_created_at)
    if not rows:
        # Fallback: all HTML-ready recipes, oldest first (allows repost)
        rows = [r for r in repo.list_recipes() if r.card_html_created_at]
        rows.sort(key=lambda r: r.card_html_created_at)
    if seeds:
        rows = [r for r in rows if r.id in seeds]
    return rows[:limit] if limit else rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_existing_ig_post(recipe_name: str, wp_url: str) -> str | None:
    """Check IG feed for a recently published post matching this recipe. Returns permalink or None."""
    from publishers.instagram import list_recent_user_media
    try:
        media = list_recent_user_media(limit=20)
        for item in media:
            caption = item.get("caption", "") or ""
            if recipe_name in caption or wp_url in caption:
                return item.get("permalink")
    except Exception as exc:
        _log.debug("ig feed check failed (non-fatal): %s", exc)
    return None


def _html_to_png(html_path: Path, out_path: Path) -> None:
    """Screenshot an HTML file to a 1080×1080 PNG using Playwright."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1080, "height": 1080})
        page.goto(f"file://{html_path.resolve()}")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(out_path), full_page=False)
        browser.close()


def _upload_image_to_wp(png_path: Path) -> str:
    """Upload a PNG to the WordPress media library and return the public source_url.

    Reads WP_URL, WP_USER, WP_APP_PASSWORD from os.environ (loaded via
    load_local_env in _base).

    Raises:
        RuntimeError: if credentials are missing or the upload fails.
    """
    import requests

    wp_url = os.environ.get("WP_URL", "").rstrip("/")
    wp_user = os.environ.get("WP_USER", "")
    wp_password = os.environ.get("WP_APP_PASSWORD", "")
    if not (wp_url and wp_user and wp_password):
        raise RuntimeError(
            "WP_URL / WP_USER / WP_APP_PASSWORD not set — cannot upload image to WP"
        )

    auth_header = base64.b64encode(f"{wp_user}:{wp_password}".encode()).decode()
    filename = png_path.name
    image_bytes = png_path.read_bytes()

    resp = requests.post(
        f"{wp_url}/wp-json/wp/v2/media",
        headers={
            "Authorization": f"Basic {auth_header}",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "image/png",
        },
        data=image_bytes,
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"WP media upload failed [{resp.status_code}]: {resp.text[:300]}"
        )
    source_url: str = resp.json()["source_url"]
    _log.info("uploaded %s → %s", filename, source_url)
    return source_url


def _template_caption(row: RecipeRow) -> str:
    """Build a Nalla's Dad voice caption from recipe fields. All fields null-safe."""
    category = (row.category or "").strip()
    hashtags = _CATEGORY_HASHTAGS.get(category, _DEFAULT_HASHTAGS)
    question = _ENGAGEMENT_QUESTIONS.get(category, _DEFAULT_QUESTION)

    # Ingredient line: top 2 ingredients by name
    ingredients = row.ingredients or []
    if ingredients:
        top_2 = [ing.item for ing in ingredients[:2] if ing.item]
        ingredient_line = (
            f"Made with {' and '.join(top_2)}." if top_2
            else "Simple wholesome ingredients, nothing fancy."
        )
    else:
        ingredient_line = "Simple wholesome ingredients, nothing fancy."

    recipe_name = row.display_name or row.name or "This recipe"
    hashtag_str = " ".join(hashtags)

    return (
        f"{recipe_name} — Nalla tested and approved! 🐾\n"
        f"\n"
        f"We tried making this for Nalla and she absolutely loved it. {ingredient_line}\n"
        f"\n"
        f"{question}\n"
        f"\n"
        f"📱 Scan the QR code in the photo for the full recipe — or follow & DM me and I'll send you the link!\n"
        f"\n"
        f"{hashtag_str}"
    )


def _build_caption(row: RecipeRow, folder: Path) -> str:
    """3-level fallback caption builder."""
    # Priority 1: pre-generated by content pipeline (stored on the row)
    gen = row.generated_content or {}
    if ig_cap := (gen.get("ig_caption") or "").strip():
        return ig_cap

    # Priority 2: ig_caption.txt from campaign folder
    cap_file = folder / "ig_caption.txt"
    if cap_file.exists():
        text = cap_file.read_text(encoding="utf-8").strip()
        if text:
            return text

    # Priority 3: template fallback (Nalla's Dad voice)
    return _template_caption(row)


def _save_ig_result(
    repo: RecipeRepository,
    row: RecipeRow,
    *,
    media_id: str,
    permalink: str,
) -> None:
    """Persist IG publish outcome into publish_status."""
    utc_iso = datetime.datetime.utcnow().isoformat()
    status = dict(row.publish_status or {})
    status["ig"] = {"state": "published", "url": permalink, "ref": media_id, "at": utc_iso}
    repo.set_publish_status(row.id, status)


# ---------------------------------------------------------------------------
# Core per-recipe action
# ---------------------------------------------------------------------------

def _do_one(repo: RecipeRepository, row: RecipeRow) -> str:
    """Publish one recipe to IG and record the result in the DB."""
    folder = campaign_folder(row)
    recipe = rehydrate_recipe(row)

    # Check IG feed for pre-existing post (idempotency)
    existing = _find_existing_ig_post(row.name, row.wp_url or "")
    if existing:
        _save_ig_result(repo, row, media_id="", permalink=existing)
        return "ig_exists"

    # Verify HTML card exists
    card_html = folder / "post_image_card.html"
    if not card_html.exists():
        raise FileNotFoundError(f"HTML card missing: {card_html}")

    # Screenshot → PNG (idempotent)
    card_png = folder / "post_image_card.png"
    if not card_png.exists():
        _html_to_png(card_html, card_png)

    # Upload to WP for public URL
    image_url = _upload_image_to_wp(card_png)

    # Build caption
    caption = _build_caption(row, folder)
    recipe.ig_caption = caption

    # Post
    result = publish_to_instagram(recipe, image_url=image_url)
    _save_ig_result(repo, row, media_id=result.media_id, permalink=result.permalink or "")
    if result.warnings:
        for w in result.warnings:
            _log.warning("ig warning for %s: %s", row.id, w)
    return "ig_post"


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def _health() -> bool:
    """Check that required IG env vars are present."""
    ig_ok = bool(
        os.environ.get("IG_ACCOUNT_ID") or os.environ.get("IG_USER_ID")
    )
    fb_ok = bool(os.environ.get("FB_PAGE_TOKEN"))
    wp_ok = bool(
        os.environ.get("WP_URL")
        and os.environ.get("WP_USER")
        and os.environ.get("WP_APP_PASSWORD")
    )
    if not ig_ok:
        _log.error("missing env: IG_ACCOUNT_ID (or IG_USER_ID)")
    if not fb_ok:
        _log.error("missing env: FB_PAGE_TOKEN")
    if not wp_ok:
        _log.error("missing env: WP_URL / WP_USER / WP_APP_PASSWORD (needed for image upload)")
    return ig_ok and fb_ok and wp_ok


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    return run_worker(
        "ig-post",
        targets_fn=_targets,
        do_one_fn=_do_one,
        health_fn=_health,
        argv=argv,
    )


if __name__ == "__main__":
    sys.exit(main())
