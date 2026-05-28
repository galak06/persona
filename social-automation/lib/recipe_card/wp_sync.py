"""WordPress sync helpers for the recipe card pipeline.

Provides four thin wrappers around the WP REST API:
  - fetch_nalla_stamp  — download the Nalla Approved stamp image bytes
  - fetch_post_data    — fetch post title / content / slug by post ID
  - upload_pdf         — upload PDF bytes to the WP Media Library
  - inject_download_button — append a PDF download CTA to post content (idempotent)

All HTTP calls use `lib.sessions.wp_client.wp_client()` which reads
WP_URL / WP_USER / WP_APP_PASSWORD from the environment.
"""

from __future__ import annotations

import logging

import httpx  # type: ignore[import-untyped]

from lib.sessions.wp_client import wp_client  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

_NALLA_STAMP_MEDIA_ID = 3717
_DOWNLOAD_BUTTON_MARKER = "recipe-card-download"
_DOWNLOAD_BUTTON_TEMPLATE = (
    '<div class="{marker}" style="text-align:center;margin:30px 0;">'
    '<a href="{pdf_url}" download'
    ' style="display:inline-block;background:#E87722;color:#fff;'
    "padding:14px 32px;border-radius:30px;font-weight:bold;"
    'text-decoration:none;font-size:16px;">'
    "\U0001f43e Download Recipe Card (PDF)"
    "</a>"
    "</div>"
)


def fetch_nalla_stamp(stamp_media_id: int = _NALLA_STAMP_MEDIA_ID) -> bytes:
    """Fetch the stamp image from WP Media Library by media ID.

    Args:
        stamp_media_id: WP media ID of the stamp image. Pass 0 to skip.

    Returns:
        Raw image bytes, or b'' if stamp_media_id is 0.

    Raises:
        ValueError: If the media item is missing or the source_url is empty.
        httpx.HTTPStatusError: On non-2xx from either request.
    """
    if stamp_media_id == 0:
        return b""
    with wp_client() as client:
        resp = client.get(f"/wp-json/wp/v2/media/{stamp_media_id}")
        resp.raise_for_status()
        data = resp.json()

    source_url: str = data.get("source_url", "")
    if not source_url:
        raise ValueError(
            f"Media {_NALLA_STAMP_MEDIA_ID} has no source_url — "
            "check the WP Media Library."
        )

    logger.debug("Downloading Nalla stamp from %s", source_url)
    img_resp = httpx.get(source_url, timeout=30.0)
    img_resp.raise_for_status()
    logger.info(
        "Fetched stamp: %d bytes (media_id=%d)",
        len(img_resp.content),
        stamp_media_id,
    )
    return img_resp.content


def fetch_post_data(post_id: int) -> dict:
    """Fetch post title, content, and slug from the WP REST API.

    Args:
        post_id: WordPress post ID.

    Returns:
        Dict with keys ``title`` (str), ``content`` (str), ``slug`` (str).

    Raises:
        ValueError: If the post is not found, not published, or the response
            is missing expected fields.
        httpx.HTTPStatusError: On non-2xx HTTP response.
    """
    with wp_client() as client:
        resp = client.get(f"/wp-json/wp/v2/posts/{post_id}")

    if resp.status_code == 404:
        raise ValueError(f"Post {post_id} not found (404).")
    resp.raise_for_status()

    data = resp.json()
    status: str = data.get("status", "")
    if status != "publish":
        raise ValueError(
            f"Post {post_id} is not published (status={status!r}). "
            "Only published posts are supported."
        )

    try:
        title: str = data["title"]["rendered"]
        content: str = data["content"]["rendered"]
        slug: str = data["slug"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"Post {post_id} response is missing expected fields: {exc}"
        ) from exc

    logger.info("Fetched post %d: slug=%r, title=%r", post_id, slug, title[:60])
    return {"title": title, "content": content, "slug": slug}


def upload_pdf(pdf_bytes: bytes, filename: str) -> str:
    """Upload PDF bytes to the WP Media Library.

    Args:
        pdf_bytes: Raw PDF content.
        filename:  Filename to register in the Media Library (e.g. ``"card.pdf"``).

    Returns:
        The public ``source_url`` of the newly created media item.

    Raises:
        ValueError: If the upload response does not contain a source_url.
        httpx.HTTPStatusError: On non-2xx HTTP response.
    """
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "application/pdf",
    }
    with wp_client() as client:
        resp = client.post(
            "/wp-json/wp/v2/media",
            content=pdf_bytes,
            headers=headers,
        )
    resp.raise_for_status()

    source_url: str = resp.json().get("source_url", "")
    if not source_url:
        raise ValueError(
            f"WP media upload succeeded but response has no source_url "
            f"(filename={filename!r}, status={resp.status_code})."
        )

    logger.info(
        "Uploaded PDF %r (%d bytes) → %s", filename, len(pdf_bytes), source_url
    )
    return source_url


def remove_download_button(post_id: int) -> None:
    """Remove the recipe-card-download div block from a post's content.

    Finds the ``<div class="recipe-card-download"...>`` opening tag, then
    locates the matching ``</div>`` and strips the entire block.  No-ops if
    the marker is not present.

    Args:
        post_id: WordPress post ID.

    Raises:
        ValueError: If the post is not found or not published.
        httpx.HTTPStatusError: On non-2xx HTTP response.
    """
    post = fetch_post_data(post_id)
    content: str = post["content"]

    if _DOWNLOAD_BUTTON_MARKER not in content:
        logger.info("Post %d has no download button — nothing to remove.", post_id)
        return

    start = content.find(f'<div class="{_DOWNLOAD_BUTTON_MARKER}"')
    if start == -1:
        logger.warning(
            "Marker string found but opening <div> tag not located in post %d — skipping removal.",
            post_id,
        )
        return

    # Walk forward to find the matching closing </div> (depth-tracked).
    search_from = start + len("<div")
    depth = 1
    pos = search_from
    end: int = len(content)  # fallback: strip to end-of-string if unmatched
    while depth > 0 and pos < len(content):
        open_tag = content.find("<div", pos)
        close_tag = content.find("</div>", pos)
        if close_tag == -1:
            break
        if open_tag != -1 and open_tag < close_tag:
            depth += 1
            pos = open_tag + 4
        else:
            depth -= 1
            pos = close_tag + len("</div>")
            if depth == 0:
                end = pos

    cleaned = (content[:start] + content[end:]).rstrip("\n")

    with wp_client() as client:
        resp = client.patch(
            f"/wp-json/wp/v2/posts/{post_id}",
            json={"content": cleaned},
        )
    resp.raise_for_status()
    logger.info("Removed download button from post %d.", post_id)


def inject_download_button(post_id: int, pdf_url: str) -> bool:
    """Append a PDF download button to a post's content. Idempotent.

    If the marker ``recipe-card-download`` is already present in the post
    content the function returns ``False`` without making any API call.
    Otherwise it appends the styled button block and PATCHes the post.

    Args:
        post_id: WordPress post ID.
        pdf_url: Public URL of the uploaded PDF.

    Returns:
        ``True`` if the button was injected, ``False`` if it was already present.

    Raises:
        ValueError: If the post is not found or not published.
        httpx.HTTPStatusError: On non-2xx HTTP response.
    """
    post = fetch_post_data(post_id)
    content: str = post["content"]

    if _DOWNLOAD_BUTTON_MARKER in content:
        logger.info(
            "Post %d already has download button — skipping injection.", post_id
        )
        return False

    button_html = _DOWNLOAD_BUTTON_TEMPLATE.format(
        marker=_DOWNLOAD_BUTTON_MARKER,
        pdf_url=pdf_url,
    )
    updated_content = content + "\n" + button_html

    with wp_client() as client:
        resp = client.patch(
            f"/wp-json/wp/v2/posts/{post_id}",
            json={"content": updated_content},
        )
    resp.raise_for_status()

    logger.info("Injected download button into post %d → %s", post_id, pdf_url)
    return True
