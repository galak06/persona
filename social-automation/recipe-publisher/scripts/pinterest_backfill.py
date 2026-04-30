"""Backfill Pinterest pins for recipes already in published_recipes.json.

For each entry without pinterest_pin_ids:
  1. Resolve the WP post URL + excerpt (fallback description source).
  2. Look up the 4 slide images in the WP media library by slug.
  3. Post 4 Pins via publishers.pinterest.create_single_pin (all → WP post URL).
  4. Record pin_ids back on the entry.

Run:
    python scripts/pinterest_backfill.py                  # dry-run (default)
    python scripts/pinterest_backfill.py --no-dry-run     # actually post
    python scripts/pinterest_backfill.py --slug foo       # one recipe only
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

# Allow `python scripts/pinterest_backfill.py` from the recipe-publisher dir.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from publishers.pinterest import create_single_pin  # noqa: E402

logger = logging.getLogger("pinterest_backfill")

STATE_PATH = ROOT / "state" / "published_recipes.json"
SETTINGS_PATH = ROOT.parent / ".claude" / "settings.local.json"
PIN_GAP_SEC = 3.0
EXPECTED_SLIDES = 4


def load_env_from_settings() -> None:
    """Mirror the convention in scripts/content_pipeline.py: hydrate os.environ
    from the 'env' block in social-automation/.claude/settings.local.json."""
    if not SETTINGS_PATH.exists():
        return
    try:
        settings = json.loads(SETTINGS_PATH.read_text())
    except json.JSONDecodeError:
        return
    for k, v in (settings.get("env", {}) or {}).items():
        os.environ.setdefault(k, v)


def _wp_auth_header() -> str:
    user = os.environ["WP_USER"]
    pw = os.environ["WP_APP_PASSWORD"]
    tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return f"Basic {tok}"


def _wp_base() -> str:
    return os.environ["WP_URL"].rstrip("/") + "/wp-json/wp/v2"


def fetch_post(post_id: int, client: httpx.Client) -> dict:
    r = client.get(
        f"/posts/{post_id}",
        headers={"Authorization": _wp_auth_header()},
        params={"_fields": "id,link,title,excerpt"},
    )
    r.raise_for_status()
    return r.json()


def fetch_slide_urls(slug: str, client: httpx.Client) -> list[str]:
    """Find the 4 carousel slides uploaded for this recipe.

    IG publisher uploaded them as `{slug}-slide-{01..04}.jpg`, so search by
    slug and pick the 4 whose filename matches the suffix.
    """
    r = client.get(
        "/media",
        headers={"Authorization": _wp_auth_header()},
        params={"search": f"{slug}-slide", "per_page": 20, "_fields": "id,source_url,slug"},
    )
    r.raise_for_status()
    items = r.json()
    # Pick the 4 whose slug contains `{slug}-slide-NN`; sort by the NN.
    candidates = [
        (m, m["slug"])
        for m in items
        if f"{slug}-slide-" in m.get("slug", "")
    ]
    candidates.sort(key=lambda t: t[1])
    return [m["source_url"] for m, _ in candidates[:EXPECTED_SLIDES]]


def excerpt_to_text(excerpt: dict) -> str:
    """Strip WP's <p>...</p> wrapping from excerpt.rendered."""
    import re

    raw = (excerpt or {}).get("rendered", "")
    return re.sub(r"<[^>]+>", "", raw).strip()


def compose_description(title: str, excerpt_text: str, slide_index: int) -> str:
    base = excerpt_text or (
        f"Homemade {title.lower()} for dogs — simple, vet-conscious ingredients."
    )
    tails = {
        1: "Full printable recipe on dogfoodandfun.com.",
        2: "Ingredients, portions, and prep notes at dogfoodandfun.com.",
        3: "Step-by-step instructions at dogfoodandfun.com.",
        4: "Save this for your next dog meal prep — recipe at dogfoodandfun.com.",
    }
    return f"{base}  {tails.get(slide_index, 'Full recipe at dogfoodandfun.com.')}"


def backfill_entry(
    entry: dict,
    client: httpx.Client,
    *,
    dry_run: bool,
) -> list[str]:
    slug = entry["slug"]
    post = fetch_post(entry["wp_post_id"], client)
    wp_url = post["link"]
    title = (post.get("title", {}) or {}).get("rendered", entry["title"])
    excerpt = excerpt_to_text(post.get("excerpt", {}))

    slide_urls = fetch_slide_urls(slug, client)
    if len(slide_urls) != EXPECTED_SLIDES:
        raise RuntimeError(
            f"slug={slug}: found {len(slide_urls)} slide images in WP media, expected {EXPECTED_SLIDES}"
        )

    logger.info("backfill: %s → %s (%d slides)", slug, wp_url, len(slide_urls))
    if dry_run:
        for i, url in enumerate(slide_urls, start=1):
            desc = compose_description(title, excerpt, i)
            logger.info("  DRY pin %d: %s | %s", i, url, desc[:80])
        return []

    pin_ids: list[str] = []
    for i, url in enumerate(slide_urls, start=1):
        pin = create_single_pin(
            image_url=url,
            link=wp_url,
            title=title,
            description=compose_description(title, excerpt, i),
            alt_text=title,
        )
        pin_ids.append(pin.pin_id)
        logger.info("  pin %d/%d created: %s", i, len(slide_urls), pin.pin_id)
        if i < len(slide_urls):
            time.sleep(PIN_GAP_SEC)
    return pin_ids


def save_state(data: list[dict]) -> None:
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(STATE_PATH)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    load_env_from_settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-dry-run", action="store_true")
    parser.add_argument("--slug", default=None, help="only backfill this one slug")
    args = parser.parse_args(argv)
    dry = not args.no_dry_run

    data: list[dict] = json.loads(STATE_PATH.read_text())
    todo = [
        e
        for e in data
        if not e.get("pinterest_pin_ids")
        and (args.slug is None or e.get("slug") == args.slug)
    ]
    if not todo:
        print("nothing to backfill")
        return 0

    print(f"backfilling {len(todo)} recipe(s) [dry_run={dry}]")
    with httpx.Client(base_url=_wp_base(), timeout=30.0) as client:
        for entry in todo:
            try:
                pin_ids = backfill_entry(entry, client, dry_run=dry)
            except Exception as exc:  # noqa: BLE001
                logger.exception("failed backfill for slug=%s", entry.get("slug"))
                continue
            if pin_ids and not dry:
                entry["pinterest_pin_ids"] = pin_ids
                save_state(data)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
