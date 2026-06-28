"""WordPress publisher for content ideas (non-recipe blog posts)."""
from __future__ import annotations

import logging
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import NamedTuple

import httpx
from markdown import markdown

from generators.image import GeneratedImage
from publishers._wordpress_ideas_helpers import call_gemini, generate_idea_image

logger = logging.getLogger(__name__)

# Make lib/ importable for affiliate block helpers
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "lib"))


class IdeaPublishResult(NamedTuple):
    post_id: str
    permalink: str
    featured_image_url: str
    warnings: list[str]


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[-\s]+", "-", text).strip("-")


def _extract_excerpt(body_markdown: str) -> str:
    for line in body_markdown.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            clean = re.sub(r"[*_`\[\]()#>]", "", stripped)
            return clean[:160]
    return ""


def _upload_idea_media(
    client: httpx.Client,
    image: GeneratedImage,
    slug: str,
    warnings: list[str],
) -> tuple[int, str]:
    if image.bytes_:
        content = image.bytes_
        content_type = image.content_type or "image/png"
    else:
        r = httpx.get(image.url, timeout=60.0)
        r.raise_for_status()
        content = r.content
        content_type = r.headers.get("Content-Type", "image/png")

    resp = client.post(
        "/wp-json/wp/v2/media",
        content=content,
        headers={
            "Content-Type": content_type,
            "Content-Disposition": f'attachment; filename="{slug}.png"',
        },
        timeout=60.0,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"media upload failed: {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    return int(data["id"]), data["source_url"]


def _attach_affiliate_block(
    html: str, slug: str, title: str, warnings: list[str]
) -> str:
    """Inject affiliate product block before the FAQ — best-effort, never fatal."""
    tag = os.environ.get("AMAZON_ASSOCIATES_TAG", "").strip()
    if not tag:
        return html
    try:
        from recipe_products import (
            insert_or_replace_block,
            load_catalog,
            pick_products,
            render_block,
        )
        catalog = load_catalog()
        products = pick_products(slug, title, catalog, limit=2)
        if not products:
            return html
        block = render_block(products, slug, associates_tag=tag)
        return insert_or_replace_block(html, block)
    except Exception as exc:
        warnings.append(f"affiliate block skipped: {exc}")
        return html


def _resolve_tags(
    client: httpx.Client, tag_names: list[str], warnings: list[str]
) -> list[int]:
    ids: list[int] = []
    for name in tag_names:
        tag_slug = name.lower().replace(" ", "-")
        existing = client.get(
            "/wp-json/wp/v2/tags", params={"slug": tag_slug}, timeout=30.0
        ).json()
        if existing:
            ids.append(int(existing[0]["id"]))
            continue
        r = client.post(
            "/wp-json/wp/v2/tags",
            json={"name": name, "slug": tag_slug},
            timeout=30.0,
        )
        if r.status_code >= 400:
            warnings.append(f"failed to create tag {name!r}: {r.status_code}")
            continue
        ids.append(int(r.json()["id"]))
    return ids


def _set_surerank_meta(
    client: httpx.Client,
    post_id: int,
    title: str,
    excerpt: str,
    warnings: list[str],
) -> None:
    payload = {
        "metaData": {"page_title": title, "page_description": excerpt},
        "post_id": str(post_id),
    }
    r = client.post(
        "/wp-json/surerank/v1/post/settings",
        params={"_locale": "user"},
        json=payload,
        timeout=30.0,
    )
    if r.status_code >= 400:
        warnings.append(
            f"SureRank meta failed for post_id={post_id}: {r.status_code} {r.text[:200]}"
        )


def _image_brief_for_idea(idea: dict, enrichment: dict | None) -> str:
    """Build a content-appropriate hero image brief via a short LLM call."""
    topic = idea.get("topic", "")
    category = idea.get("category", "")
    nalla_angle = (enrichment or {}).get("content_brief", {}).get("nalla_angle", "")

    prompt = (
        f"Write a 1-sentence image generation brief (15-25 words) for a blog hero photo.\n"
        f"Topic: {topic}\n"
        f"Category: {category}\n"
        f"Dog context: {nalla_angle or 'Nalla, a fluffy tan-and-black shepherd mix'}\n\n"
        f"Brief must describe a SCENE (not text/graphics) that visually represents the topic.\n"
        f"Include Nalla the dog naturally in the scene.\n"
        f"Output only the brief, no quotes, no preamble."
    )

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return f"Nalla the dog in a scene related to {topic}, natural home setting"

    model = os.getenv("GEMINI_CONTENT_MODEL", "gemini-2.5-flash")
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 64,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    resp = httpx.post(endpoint, params={"key": key}, json=payload, timeout=20.0)
    resp.raise_for_status()
    parts = resp.json()["candidates"][0]["content"]["parts"]
    return parts[0]["text"].strip()


def publish_idea_to_wordpress(
    idea: dict,
    enrichment: dict | None,
    *,
    status: str = "publish",
) -> IdeaPublishResult:
    """Generate and publish a blog post from an approved content idea."""
    warnings: list[str] = []
    e = enrichment or {}

    topic = idea.get("topic", "")
    title = e.get("suggested_title") or topic
    slug = _slugify(title)
    category_slug = (idea.get("category") or "lifestyle").lower().replace(" ", "-")
    primary_keyword = e.get("primary_keyword") or idea.get("target_keyword") or ""
    secondary_keywords: list[str] = e.get("secondary_keywords") or []
    tag_names = list(
        dict.fromkeys(
            n for n in [primary_keyword] + secondary_keywords[:2] + [category_slug] if n
        )
    )

    body_markdown = call_gemini(idea, e)
    brief = _image_brief_for_idea(idea, enrichment)
    image = generate_idea_image(brief=brief, alt_hint=topic)
    alt_text = image.alt_text or topic
    excerpt = _extract_excerpt(body_markdown)

    wp_url = os.environ["WP_URL"].rstrip("/")
    wp_user = os.environ["WP_USER"]
    wp_pass = os.environ["WP_APP_PASSWORD"]

    with httpx.Client(
        base_url=wp_url,
        auth=(wp_user, wp_pass),
        timeout=30.0,
        headers={"User-Agent": "recipe-publisher/0.1 (+dogfoodandfun.com)"},
    ) as client:
        media_id, media_source_url = _upload_idea_media(client, image, slug, warnings)

        cat_r = client.get(
            "/wp-json/wp/v2/categories", params={"slug": category_slug}, timeout=30.0
        )
        cat_data = cat_r.json() if cat_r.status_code < 400 else []
        cat_id: int = int(cat_data[0]["id"]) if cat_data else 1

        tag_ids = _resolve_tags(client, tag_names, warnings)

        body_html = markdown(body_markdown, extensions=["extra", "sane_lists", "smarty"])
        body_html = _attach_affiliate_block(body_html, slug, title, warnings)
        hero = (
            f'<figure><img src="{media_source_url}" '
            f'alt="{alt_text}" class="wp-post-image"></figure>'
        )
        full_body = (
            '<div class="dff-idea-body" style="max-width:720px;margin:0 auto;">'
            f"{hero}{body_html}"
            "</div>"
        )

        post_payload = {
            "title": title,
            "slug": slug,
            "status": status,
            "content": full_body,
            "excerpt": excerpt,
            "featured_media": media_id,
            "categories": [cat_id],
            "tags": tag_ids,
            "template": "",
            "meta": {
                "fifu_image_url": media_source_url,
                "fifu_image_alt": alt_text,
                "_elementor_edit_mode": "",
            },
        }
        resp = client.post("/wp-json/wp/v2/posts", json=post_payload, timeout=30.0)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"post create failed: {resp.status_code} {resp.text[:300]}"
            )
        post = resp.json()
        post_id = int(post["id"])
        permalink = post["link"]

        _set_surerank_meta(client, post_id, title, excerpt, warnings)

        alt_r = client.post(
            f"/wp-json/wp/v2/media/{media_id}",
            json={"alt_text": alt_text},
            timeout=30.0,
        )
        if alt_r.status_code >= 400:
            warnings.append(f"alt_text set failed for media_id={media_id}")

    return IdeaPublishResult(
        post_id=str(post_id),
        permalink=permalink,
        featured_image_url=media_source_url,
        warnings=warnings,
    )
