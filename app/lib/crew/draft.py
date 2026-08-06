"""WordPress draft-creation step for the CrewAI content pipeline.

Callers must only invoke `create_wp_draft` after `lib.crew.validate.validate_draft`
has returned `passed=True` -- this module does not re-check validation itself
(single responsibility: this step only knows how to create a draft and record
it, not how to judge one).

POSTs to WordPress with `status: "draft"` hardcoded -- no parameter, env var,
or flag anywhere in this module can override that; this pipeline never
auto-publishes (hard constraint, not a default that happens to be draft
today). Modeled directly on
`recipe-publisher/publishers/wordpress.py::publish_to_wordpress`'s request
shape (`client.post("/wp-json/wp/v2/posts", json={...})`, the same
`resp.status_code >= 400` failure check, the same `post["id"]`/`post["link"]`
response fields).

Also handles the hero-image + WordPress-presentation concerns that
`recipe-publisher/publishers/wordpress_ideas.py::publish_idea_to_wordpress`
proved out for this exact site/theme: generate a topic-appropriate hero
image (`lib.crew.wp_image.generate_wp_image`), upload it to the WP media
library (`lib.crew.wp_media.upload_wp_media`), wrap the post body in the
`dff-idea-body` container + inline hero `<figure>` the live theme expects,
and set `featured_media` + FIFU plugin meta + an Elementor-edit-mode clear
on the create POST. The image step is best-effort: any failure (no brief
supplied, missing `GEMINI_API_KEY`, every Imagen provider failing, or the
media upload itself failing) is logged and the draft is still created
without a featured image, matching this repo's established "best-effort,
never blocks publish" convention (see `_maybe_attach_affiliate_block`'s
docstring in `recipe-publisher/publishers/wordpress.py`). Category/tag
resolution and SureRank SEO-meta setting (also present in the reference
implementation) are deliberately out of scope here -- cosmetic, not
required for a correctly-rendering draft.

On success, records the result on the originating `content_ideas` row via
`lib.ideas_db.set_wp_result` + `lib.ideas_db.update_status(..., "wp_draft")`
so a human can later find "which ideas already became drafts" via
`list_ideas(status="wp_draft")`.
"""

from __future__ import annotations

import mimetypes
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from lib import ideas_db
from lib.crew.categorizer import execute_categorizer_crew
from lib.crew.draft_body import build_wrapped_body, slugify
from lib.crew.draft_category import CategorizeFn, resolve_category_id
from lib.crew.wp_image import GeneratedImage, generate_wp_image
from lib.crew.wp_media import upload_wp_media
from lib.observability import get_logger
from lib.sessions.wp_client import wp_client

logger = get_logger(__name__)

SetWpResultFn = Callable[[str, str, str], bool]
UpdateStatusFn = Callable[[str, str], bool]
GenerateImageFn = Callable[..., GeneratedImage]
UploadMediaFn = Callable[[httpx.Client, GeneratedImage, str], tuple[int, str]]


class DraftCreationError(RuntimeError):
    """Raised when the WordPress REST POST itself fails (network/4xx/5xx).

    Never raised for a validation rejection -- callers must not invoke
    `create_wp_draft` at all when `ValidationResult.passed` is False; that
    is a caller-contract violation, not something this module detects.
    Also never raised for a hero-image/media-upload failure -- that path is
    best-effort (see module docstring).
    """


@dataclass(frozen=True)
class DraftResult:
    """The real WordPress draft created. `post_url` is always WordPress's
    own `link` field from the create response (a real, draft-status
    permalink), never derived/guessed locally."""

    idea_id: str
    post_id: int
    post_url: str


def _attach_hero_image(
    client: httpx.Client,
    *,
    idea_id: str,
    slug: str,
    title: str,
    image_brief: str,
    mascot_name: str,
    generate_image_fn: GenerateImageFn,
    upload_media_fn: UploadMediaFn,
    reference_image_bytes: bytes | None = None,
    reference_image_mime: str = "image/png",
) -> tuple[int, str, str]:
    """Best-effort hero-image generation + WP media upload.

    Never raises -- any failure along the way (no brief supplied, provider
    failure, upload failure) is logged and returns `(0, "", "")`. The
    caller posts the draft WITHOUT a featured image in that case.
    """
    if not image_brief:
        logger.info("crew_draft_image_skipped_no_brief", idea_id=idea_id)
        return 0, "", ""

    try:
        image = generate_image_fn(
            brief=image_brief,
            alt_hint=title,
            mascot_name=mascot_name,
            reference_image_bytes=reference_image_bytes,
            reference_image_mime=reference_image_mime,
        )
    except Exception as exc:  # defensive: generate_wp_image already catches provider errors itself
        logger.warning("crew_draft_image_generation_failed", idea_id=idea_id, error=str(exc))
        return 0, "", ""

    if not image.bytes_:
        logger.warning("crew_draft_image_generation_placeholder", idea_id=idea_id)
        return 0, "", ""

    try:
        media_id, media_source_url = upload_media_fn(client, image, slug)
    except Exception as exc:
        logger.warning("crew_draft_media_upload_failed", idea_id=idea_id, error=str(exc))
        return 0, "", ""

    return media_id, media_source_url, image.alt_text or title


def _read_reference_image(path: Path | None, idea_id: str) -> tuple[bytes | None, str]:
    """Best-effort read of an optional per-brand reference photo. Never
    raises -- a missing/unreadable file just means no reference (logged),
    not a draft-creation failure."""
    if path is None:
        return None, "image/png"
    try:
        data = path.read_bytes()
    except OSError as exc:
        logger.warning(
            "crew_draft_reference_image_unreadable", idea_id=idea_id, path=str(path), error=str(exc)
        )
        return None, "image/png"
    mime, _ = mimetypes.guess_type(path.name)
    return data, mime or "image/png"


def create_wp_draft(
    *,
    idea_id: str,
    title: str,
    body_html: str,
    image_brief: str = "",
    mascot_name: str = "",
    category_name: str = "",
    reference_image_path: Path | None = None,
    set_wp_result_fn: SetWpResultFn = ideas_db.set_wp_result,
    update_status_fn: UpdateStatusFn = ideas_db.update_status,
    generate_image_fn: GenerateImageFn = generate_wp_image,
    upload_media_fn: UploadMediaFn = upload_wp_media,
    categorize_fn: CategorizeFn = execute_categorizer_crew,
) -> DraftResult:
    """POST a `status: draft` WordPress post, then record it on the idea row.

    `image_brief` (see `lib.crew.wp_image.build_image_brief`) drives a
    best-effort hero-image generation + upload step; pass `""` (the
    default) to skip the image step entirely and post a `dff-idea-body`-
    wrapped draft with no hero figure and no `featured_media`/FIFU meta.

    `category_name` (optional): the originating idea's free-text category
    (`content_ideas.category`, e.g. 'Recipes', 'GPS/Gear') -- resolved
    against the brand's real WP categories (see
    `lib.crew.draft_category.resolve_category_id`: exact name match first,
    then a content-based categorizer fallback). No match (or `""`) leaves
    the post uncategorized rather than guessing.

    `reference_image_path` (optional): a real photo of the brand's persona/
    mascot (e.g. `$BRAND_DIR/data/assets/persona_mascot_reference.png`) to
    condition hero-image generation on, so it depicts them instead of a
    generic AI-invented person/dog -- see `lib.crew.wp_image.generate_wp_image`.
    A missing file is a soft skip (logged), not an error -- this pipeline is
    brand-agnostic and most brands won't have this asset.

    Raises `DraftCreationError` on a failed WP POST (network/4xx/5xx) --
    intentionally NOT swallowed, since a draft-creation failure here means
    nothing was written anywhere and the caller needs to know loudly. A
    failed `content_ideas` update AFTER a successful WP POST is logged, not
    raised -- the real draft already exists on WordPress at that point; a
    DB-write hiccup shouldn't be reported as if draft creation itself failed.
    """
    slug = slugify(title)
    reference_bytes, reference_mime = _read_reference_image(reference_image_path, idea_id)

    with wp_client() as client:
        media_id, media_source_url, alt_text = _attach_hero_image(
            client,
            idea_id=idea_id,
            slug=slug,
            title=title,
            image_brief=image_brief,
            mascot_name=mascot_name,
            generate_image_fn=generate_image_fn,
            upload_media_fn=upload_media_fn,
            reference_image_bytes=reference_bytes,
            reference_image_mime=reference_mime,
        )
        full_body = build_wrapped_body(
            body_html, media_source_url=media_source_url, alt_text=alt_text
        )
        category_id = resolve_category_id(
            client, category_name, idea_id, title=title, categorize_fn=categorize_fn
        )

        payload: dict[str, Any] = {"title": title, "content": full_body, "status": "draft"}
        if category_id:
            payload["categories"] = [category_id]
        if media_id:
            payload["featured_media"] = media_id
            payload["meta"] = {
                "fifu_image_url": media_source_url,
                "fifu_image_alt": alt_text,
                "_elementor_edit_mode": "",
            }
        resp = client.post("/wp-json/wp/v2/posts", json=payload)

    if resp.status_code >= 400:
        raise DraftCreationError(f"WP post create failed: {resp.status_code} {resp.text}")

    post = resp.json()
    post_id = int(post["id"])
    post_url = str(post["link"])
    logger.info(
        "crew_draft_wp_post_created",
        idea_id=idea_id,
        post_id=post_id,
        post_url=post_url,
        featured_media=media_id,
    )

    set_ok = set_wp_result_fn(idea_id, str(post_id), post_url)
    status_ok = update_status_fn(idea_id, "wp_draft")
    if not set_ok or not status_ok:
        logger.warning(
            "crew_draft_ideas_db_update_incomplete",
            idea_id=idea_id,
            post_id=post_id,
            set_wp_result_ok=set_ok,
            update_status_ok=status_ok,
        )

    return DraftResult(idea_id=idea_id, post_id=post_id, post_url=post_url)
