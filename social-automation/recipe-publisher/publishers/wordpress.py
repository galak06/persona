"""WordPress publisher.

- Application Password auth (HTTP Basic with app password as the password).
- Media upload from URL (downloads image bytes, POSTs multipart to /wp/v2/media).
- Post create with markdown → HTML body conversion.
- SureRank page_description set via /surerank/v1/post/settings (never leaves %post_content% default).
- Recipe schema (JSON-LD) injected into post_content as an HTML block.
- FIFU featured-image field set via post meta if FIFU is the active image strategy.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import markdown as md
from generators.image import GeneratedImage
from generators.recipe import Recipe

# Make the project-level lib/ importable so we can attach the affiliate
# "Our Pick: Tools Used in This Recipe" block before publishing.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "lib"))

from recipe_products import (  # noqa: E402  (lib path inserted above)
    insert_or_replace_block,
    load_catalog,
    pick_products,
    render_block,
)

logger = logging.getLogger(__name__)


@dataclass
class WPPublishResult:
    post_id: int
    permalink: str
    featured_image_url: str
    warnings: list[str] = field(default_factory=list)


class WordPressError(RuntimeError):
    pass


def _client() -> httpx.Client:
    # Standardized on the social-automation project convention.
    # Legacy WP_BASE_URL / WP_APP_PASSWORD_USER aliases were removed in
    # Stage 4 — set WP_URL / WP_USER / WP_APP_PASSWORD instead.
    base = os.environ["WP_URL"].rstrip("/")
    user = os.environ["WP_USER"]
    pw = os.environ["WP_APP_PASSWORD"]
    return httpx.Client(
        base_url=base,
        auth=(user, pw),
        timeout=60.0,
        headers={"User-Agent": "recipe-publisher/0.1 (+dogfoodandfun.com)"},
    )


def publish_to_wordpress(
    recipe: Recipe,
    image: GeneratedImage,
    *,
    status: str = "publish",
    category_slug: str = "recipes",
) -> WPPublishResult:
    """Publish recipe to WordPress. Returns metadata needed downstream for IG."""
    warnings: list[str] = []
    with _client() as client:
        media_id, media_source_url = _upload_media(client, image, recipe)
        cat_id = _resolve_category(client, category_slug, warnings)
        tag_ids = _resolve_tags(client, recipe.tags, warnings)

        body_html = _compose_body(recipe, media_source_url, image.alt_text)
        post_payload = {
            "title": recipe.title,
            "slug": recipe.slug,
            "status": status,
            "content": body_html,
            "excerpt": recipe.meta_description,
            "featured_media": media_id,
            "categories": [cat_id] if cat_id else [],
            "tags": tag_ids,
            # FIFU (Featured Image From URL) is active on this site and overrides
            # the core featured image via its own meta — set both fields or the
            # front-end shows no image even with featured_media set. Elementor
            # meta is aggressively cleared so Astra renders the stored HTML body
            # instead of Elementor's (empty) builder data. Using the default
            # page template for the same reason.
            "template": "",
            "meta": {
                "fifu_image_url": media_source_url,
                "fifu_image_alt": image.alt_text,
                "_elementor_edit_mode": "",
                "_elementor_template_type": "",
                "_elementor_version": "",
                "_elementor_data": "",
                "_elementor_css": "",
                "_elementor_page_assets": "",
            },
        }
        resp = client.post("/wp-json/wp/v2/posts", json=post_payload)
        if resp.status_code >= 400:
            raise WordPressError(f"post create failed: {resp.status_code} {resp.text}")
        post = resp.json()
        post_id = int(post["id"])
        permalink = post["link"]

        _set_surerank_meta(client, post_id, recipe, warnings)
        _set_image_alt(client, media_id, image.alt_text, warnings)

    return WPPublishResult(
        post_id=post_id,
        permalink=permalink,
        featured_image_url=media_source_url,
        warnings=warnings,
    )


# ---------- helpers ----------


def _compose_body(recipe: Recipe, image_url: str, alt_text: str) -> str:
    """Prepend a hero image, convert markdown to HTML, append JSON-LD schemas.

    The hero image is inlined in post_content so it renders on the post page
    regardless of theme/Elementor-plugin behavior. featured_media + FIFU handle
    thumbnail display on category/archive pages separately.

    Two JSON-LD blocks are appended: `Recipe` (rich-result card with
    time/image/yield) and — when the recipe carries Q&A pairs — `FAQPage`
    (pairs with the H3 question headers in the body to win 'People Also Ask'
    and featured-snippet real estate).
    """
    hero = (
        f'<figure class="wp-block-image size-full">'
        f'<img src="{image_url}" alt="{_escape_attr(alt_text)}" />'
        f"</figure>"
    )
    html = md.markdown(
        recipe.body_markdown,
        extensions=["extra", "sane_lists", "smarty"],
    )
    html = _maybe_attach_affiliate_block(html, recipe)
    schema_blocks = [_jsonld_block(_recipe_jsonld(recipe, image_url=image_url))]
    faq_schema = _faq_jsonld(recipe)
    if faq_schema is not None:
        schema_blocks.append(_jsonld_block(faq_schema))
    return f"{hero}\n\n{html}\n\n" + "\n\n".join(schema_blocks) + "\n"


def _maybe_attach_affiliate_block(html: str, recipe: Recipe) -> str:
    """Inject the "Our Pick: Tools Used in This Recipe" block before the FAQ.

    Best-effort: any failure (catalog missing, no associates tag, no products
    matched) logs a warning and returns html unchanged — never blocks publish.
    """
    tag = os.environ.get("AMAZON_ASSOCIATES_TAG", "").strip()
    if not tag:
        logger.info("AMAZON_ASSOCIATES_TAG not set — skipping recipe-tools block")
        return html
    try:
        catalog = load_catalog()
        products = pick_products(recipe.slug, recipe.title, catalog, limit=3)
        if not products:
            logger.info("no recipe-tools products matched for slug=%s", recipe.slug)
            return html
        block = render_block(products, recipe.slug, associates_tag=tag)
        return insert_or_replace_block(html, block)
    except Exception as exc:
        logger.warning("recipe-tools block injection skipped: %s", exc)
        return html


def _jsonld_block(schema: dict) -> str:
    return f'<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>'


def _escape_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _recipe_jsonld(recipe: Recipe, *, image_url: str | None = None) -> dict:
    # Google's Recipe rich-result eligibility requires `image` — without it the
    # post doesn't render ratings/time/thumbnail in SERPs. `datePublished` is
    # recommended and lets freshness surface correctly.
    schema: dict = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe.title,
        "description": recipe.meta_description,
        "recipeCategory": "Dog treat",
        "recipeCuisine": "Dog food",
        "recipeIngredient": recipe.ingredients,
        "recipeInstructions": [
            {"@type": "HowToStep", "position": i + 1, "text": step}
            for i, step in enumerate(recipe.steps)
        ],
        "prepTime": f"PT{recipe.prep_minutes}M",
        "cookTime": f"PT{recipe.cook_minutes}M",
        "totalTime": f"PT{recipe.prep_minutes + recipe.cook_minutes}M",
        "recipeYield": recipe.yield_servings,
        "keywords": ", ".join(recipe.tags),
        "datePublished": datetime.now(UTC).date().isoformat(),
        "author": {"@type": "Person", "name": "Nalla's Dad"},
        "publisher": {
            "@type": "Organization",
            "name": "Dog Food & Fun",
            "url": "https://dogfoodandfun.com",
        },
    }
    if image_url:
        schema["image"] = [image_url]
    return schema


def _faq_jsonld(recipe: Recipe) -> dict | None:
    """Emit FAQPage schema from recipe.faq. Returns None if there are no pairs.

    Pairs with the `### {question}` H3 headers in the rendered body so Google
    has both structured data and on-page anchors to lift into 'People Also Ask'.
    """
    pairs = [
        p
        for p in (recipe.faq or [])
        if isinstance(p, dict) and p.get("question") and p.get("answer")
    ]
    if not pairs:
        return None
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": p["question"],
                "acceptedAnswer": {"@type": "Answer", "text": p["answer"]},
            }
            for p in pairs
        ],
    }


def _upload_media(client: httpx.Client, image: GeneratedImage, recipe: Recipe) -> tuple[int, str]:
    # Prefer in-memory bytes if generator already fetched them; else GET the URL.
    if image.bytes_:
        content = image.bytes_
        content_type = "image/png"
    else:
        r = httpx.get(image.url, timeout=60.0)
        r.raise_for_status()
        content = r.content
        content_type = r.headers.get("Content-Type", "image/png")

    filename = f"{recipe.slug}.{_ext_for(content_type)}"
    resp = client.post(
        "/wp-json/wp/v2/media",
        content=content,
        headers={
            "Content-Type": content_type,
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
    if resp.status_code >= 400:
        raise WordPressError(f"media upload failed: {resp.status_code} {resp.text}")
    data = resp.json()
    return int(data["id"]), data["source_url"]


def upload_image_to_media_library(
    image: GeneratedImage,
    *,
    filename: str,
) -> tuple[int, str]:
    """Upload a GeneratedImage to WP media library (not attached to any post).

    Used by the IG carousel publisher: Meta requires a public image_url to
    create each child container, and WP's media library is already our hosting.

    Returns (media_id, source_url).
    """
    if not image.bytes_:
        raise WordPressError("upload_image_to_media_library requires image.bytes_")
    with _client() as client:
        resp = client.post(
            "/wp-json/wp/v2/media",
            content=image.bytes_,
            headers={
                "Content-Type": image.content_type or "image/jpeg",
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
        if resp.status_code >= 400:
            raise WordPressError(f"media upload failed: {resp.status_code} {resp.text[:300]}")
        data = resp.json()
        media_id = int(data["id"])
        src = data["source_url"]
        # Best-effort alt-text set (non-fatal).
        if image.alt_text:
            client.post(
                f"/wp-json/wp/v2/media/{media_id}",
                json={"alt_text": image.alt_text},
            )
    return media_id, src


def upload_video_to_media_library(
    video_path: Path,
    *,
    filename: str,
    content_type: str = "video/mp4",
) -> tuple[int, str]:
    """Upload an mp4 to WP media library. Returns (media_id, source_url).

    Meta's Graph API needs a publicly fetchable video URL for Reels container
    creation; WP's media library is already our hosting, so we reuse it.
    """
    data = video_path.read_bytes()
    with _client() as client:
        resp = client.post(
            "/wp-json/wp/v2/media",
            content=data,
            headers={
                "Content-Type": content_type,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
        if resp.status_code >= 400:
            raise WordPressError(f"video upload failed: {resp.status_code} {resp.text[:300]}")
        body = resp.json()
    return int(body["id"]), body["source_url"]


def _ext_for(content_type: str) -> str:
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
    }.get(content_type.lower(), "png")


def _resolve_category(client: httpx.Client, slug: str, warnings: list[str]) -> int | None:
    r = client.get("/wp-json/wp/v2/categories", params={"slug": slug})
    if r.status_code >= 400 or not r.json():
        warnings.append(f"category slug={slug!r} not found; publishing uncategorized")
        return None
    return int(r.json()[0]["id"])


def _resolve_tags(client: httpx.Client, tag_names: list[str], warnings: list[str]) -> list[int]:
    ids: list[int] = []
    for name in tag_names:
        slug = name.lower().replace(" ", "-")
        existing = client.get("/wp-json/wp/v2/tags", params={"slug": slug}).json()
        if existing:
            ids.append(int(existing[0]["id"]))
            continue
        r = client.post("/wp-json/wp/v2/tags", json={"name": name, "slug": slug})
        if r.status_code >= 400:
            warnings.append(f"failed to create tag {name!r}: {r.status_code}")
            continue
        ids.append(int(r.json()["id"]))
    return ids


def _set_surerank_meta(
    client: httpx.Client, post_id: int, recipe: Recipe, warnings: list[str]
) -> None:
    payload = {
        "metaData": {
            "page_title": recipe.title,
            "page_description": recipe.meta_description,
        },
        "post_id": str(post_id),
    }
    r = client.post(
        "/wp-json/surerank/v1/post/settings",
        params={"_locale": "user"},
        json=payload,
    )
    if r.status_code >= 400:
        warnings.append(
            f"SureRank meta set failed for post_id={post_id}: {r.status_code} {r.text[:200]}"
        )


def _set_image_alt(client: httpx.Client, media_id: int, alt: str, warnings: list[str]) -> None:
    r = client.post(f"/wp-json/wp/v2/media/{media_id}", json={"alt_text": alt})
    if r.status_code >= 400:
        warnings.append(f"failed to set alt_text on media_id={media_id}: {r.status_code}")
