"""Worker FB-post — screenshot the HTML recipe card and publish as a Facebook photo post.

Poll predicate: row.card_html_created_at truthy AND row.fb_url == "".
Fallback when all HTML-ready recipes are posted: oldest HTML-ready (repost).

    python -m workers.worker_fb_post                    # dry-run plan
    python -m workers.worker_fb_post --apply --limit 1  # publish one
    python -m workers.worker_fb_post --health-check     # env probe → 0/1
"""

from __future__ import annotations

import base64
import datetime
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

_rp_root = Path(__file__).resolve().parent.parent
if str(_rp_root) not in sys.path:
    sys.path.insert(0, str(_rp_root))

from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository

from workers._base import run_worker
from workers._folder import campaign_folder

_log = logging.getLogger("workers.fb_post")

_ENGAGEMENT_QUESTIONS: dict[str, str] = {
    "Food & Diet": "Have you ever tried making homemade food for your dog?",
    "Grooming": "What's your dog's reaction when grooming day arrives?",
    "Lifestyle & Gear": "What gear has made the biggest difference for you and your dog?",
    "Training": "What's the trick your dog picked up faster than you expected?",
}
_DEFAULT_QUESTION = "Does your dog have a favourite homemade treat?"


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def _targets(repo: RecipeRepository, seeds: list[str], limit: int) -> list[RecipeRow]:
    """Primary: HTML ready, not yet posted to FB. Fallback: oldest HTML-ready (repost)."""
    rows = [r for r in repo.list_recipes() if r.card_html_created_at and not r.fb_url]
    rows.sort(key=lambda r: r.card_html_created_at)
    if not rows:
        rows = [r for r in repo.list_recipes() if r.card_html_created_at]
        rows.sort(key=lambda r: r.card_html_created_at)
    if seeds:
        rows = [r for r in rows if r.id in seeds]
    return rows[:limit] if limit else rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_existing_fb_post(wp_url: str) -> str | None:
    """Check FB page feed for a post mentioning wp_url. Returns permalink or None."""
    import requests

    page_id = os.getenv("FB_PAGE_ID", "")
    token = os.getenv("FB_PAGE_TOKEN", "")
    if not page_id or not token:
        return None
    try:
        resp = requests.get(
            f"https://graph.facebook.com/v23.0/{page_id}/feed",
            params={"fields": "id,message,permalink_url", "limit": "25", "access_token": token},
            timeout=10,
        )
        resp.raise_for_status()
        for post in resp.json().get("data", []):
            if wp_url and wp_url in (post.get("message") or ""):
                return post.get("permalink_url") or post["id"]
    except Exception as exc:
        _log.debug("fb feed check failed (non-fatal): %s", exc)
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
    """Upload a PNG to the WordPress media library and return the public source_url."""
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


@dataclass
class FBPhotoPostResult:
    post_id: str
    permalink: str


def _post_photo_to_facebook(image_url: str, caption: str) -> FBPhotoPostResult:
    """POST to /{page_id}/photos with url + message. Returns FBPhotoPostResult."""
    import requests

    page_id = os.environ.get("FB_PAGE_ID", "")
    token = os.environ.get("FB_PAGE_TOKEN", "")
    if not page_id or not token:
        raise RuntimeError("FB_PAGE_ID / FB_PAGE_TOKEN not set")

    resp = requests.post(
        f"https://graph.facebook.com/v23.0/{page_id}/photos",
        data={
            "url": image_url,
            "message": caption,
            "access_token": token,
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"FB photo post failed [{resp.status_code}]: {resp.text[:300]}"
        )
    body = resp.json()
    post_id = body.get("post_id") or body.get("id", "")
    permalink = f"https://www.facebook.com/{post_id}"
    _log.info("FB photo post published: post_id=%s", post_id)
    return FBPhotoPostResult(post_id=post_id, permalink=permalink)


def _template_caption(row: RecipeRow) -> str:
    """Build a Nalla's Dad voice FB caption. NO hashtags. Max 1 emoji. 150-200 words."""
    category = (row.category or "").strip()
    question = _ENGAGEMENT_QUESTIONS.get(category, _DEFAULT_QUESTION)

    ingredients = row.ingredients or []
    if ingredients:
        top_2 = [ing.item for ing in ingredients[:2] if ing.item]
        ingredient_line = (
            f"Made with {' and '.join(top_2)} — simple ingredients Nalla genuinely loves."
            if top_2 else
            "Simple wholesome ingredients Nalla genuinely loves."
        )
    else:
        ingredient_line = "Simple wholesome ingredients Nalla genuinely loves."

    recipe_name = row.display_name or row.name or "This recipe"

    return (
        f"Nalla approved this one before I even had a chance to photograph it. 📱\n"
        f"\n"
        f"We've been making {recipe_name} at home for a while now and it's become a staple. "
        f"{ingredient_line} "
        f"Nalla gets visibly excited when she smells these coming out — tail wagging the whole time.\n"
        f"\n"
        f"I always find homemade treats hit differently than store-bought. "
        f"You know exactly what's in them, the portions are right, "
        f"and honestly it takes less time than a trip to the pet store.\n"
        f"\n"
        f"{question}\n"
        f"\n"
        f"Scan the QR code in the photo for the full recipe — or follow & DM me and I'll send you the link!"
    )


def _build_caption(row: RecipeRow, folder: Path) -> str:
    """3-level fallback caption builder (FB rules: no hashtags, max 1 emoji)."""
    gen = row.generated_content or {}
    if fb_cap := (gen.get("fb_caption") or "").strip():
        return fb_cap

    cap_file = folder / "fb_caption.txt"
    if cap_file.exists():
        text = cap_file.read_text(encoding="utf-8").strip()
        if text:
            return text

    return _template_caption(row)


def _save_fb_result(
    repo: RecipeRepository,
    row: RecipeRow,
    *,
    post_id: str,
    permalink: str,
) -> None:
    """Persist FB publish outcome into publish_status."""
    utc_iso = datetime.datetime.utcnow().isoformat()
    status = dict(row.publish_status or {})
    status["fb"] = {"state": "published", "url": permalink, "ref": post_id, "at": utc_iso}
    repo.set_publish_status(row.id, status)


# ---------------------------------------------------------------------------
# Core per-recipe action
# ---------------------------------------------------------------------------

def _do_one(repo: RecipeRepository, row: RecipeRow) -> str:
    """Publish one recipe to FB as a photo post and record the result in the DB."""
    folder = campaign_folder(row)

    existing = _find_existing_fb_post(row.wp_url or "")
    if existing:
        _save_fb_result(repo, row, post_id="", permalink=existing)
        return "fb_exists"

    card_html = folder / "post_image_card.html"
    if not card_html.exists():
        raise FileNotFoundError(f"HTML card missing: {card_html}")

    card_png = folder / "post_image_card.png"
    if not card_png.exists():
        _html_to_png(card_html, card_png)

    image_url = _upload_image_to_wp(card_png)
    caption = _build_caption(row, folder)
    result = _post_photo_to_facebook(image_url, caption)
    _save_fb_result(repo, row, post_id=result.post_id, permalink=result.permalink)
    return "fb_post"


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def _health() -> bool:
    """Check that required FB + WP env vars are present."""
    fb_ok = bool(
        os.environ.get("FB_PAGE_ID") and os.environ.get("FB_PAGE_TOKEN")
    )
    wp_ok = bool(
        os.environ.get("WP_URL")
        and os.environ.get("WP_USER")
        and os.environ.get("WP_APP_PASSWORD")
    )
    if not fb_ok:
        _log.error("missing env: FB_PAGE_ID / FB_PAGE_TOKEN")
    if not wp_ok:
        _log.error("missing env: WP_URL / WP_USER / WP_APP_PASSWORD (needed for image upload)")
    return fb_ok and wp_ok


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    return run_worker(
        "fb-post",
        targets_fn=_targets,
        do_one_fn=_do_one,
        health_fn=_health,
        argv=argv,
    )


if __name__ == "__main__":
    sys.exit(main())
