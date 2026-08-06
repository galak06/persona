"""WordPress media-library upload for CrewAI-drafted posts' hero images.

Adapted from `recipe-publisher/publishers/wordpress_ideas.py::
_upload_idea_media` -- same defensive shape (use `image.bytes_` directly
when present, else re-download `image.url`) even though
`lib.crew.wp_image.generate_wp_image` itself never actually returns a
Pexels-style URL-only image today; kept for parity with the proven
reference and because `GeneratedImage.bytes_` is typed `bytes | None`.
"""

from __future__ import annotations

import httpx

from lib.crew.wp_image import GeneratedImage


class MediaUploadError(RuntimeError):
    """Raised when the WP media POST itself fails (network/4xx/5xx).

    Caller (`lib.crew.draft.create_wp_draft`) treats this as best-effort --
    logs a warning and posts the draft without a featured image rather than
    failing draft creation entirely.
    """


def upload_wp_media(client: httpx.Client, image: GeneratedImage, slug: str) -> tuple[int, str]:
    """Upload `image` to the WP media library. Returns `(media_id, source_url)`.

    Raises `MediaUploadError` on a failed upload (network/4xx/5xx) -- the
    caller decides how to handle that, this function does not swallow it.
    """
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
        raise MediaUploadError(f"media upload failed: {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    return int(data["id"]), str(data["source_url"])
