"""WordPress audio upload and injection helpers for the recipe card pipeline.

Provides two thin wrappers around the WP REST API:
  - upload_audio     — upload MP3 bytes to the WP Media Library
  - inject_audio_player — insert an audio block after the first paragraph (idempotent)

All HTTP calls use `lib.sessions.wp_client.wp_client()` which reads
WP_URL / WP_USER / WP_APP_PASSWORD from the environment.
"""

from __future__ import annotations

import logging
import re

from lib.recipe_card.wp_sync import fetch_post_data  # type: ignore[import-untyped]
from lib.sessions.wp_client import wp_client  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

AUDIO_MARKER = "<!-- dogfoodandfun:audio-player -->"
# Placeholder slot written by the publisher before the song exists; replaced
# in place by the real player once the reel's song is generated.
PLACEHOLDER_MARKER = "<!-- dogfoodandfun:audio-placeholder -->"


def upload_audio(mp3_bytes: bytes, filename: str) -> tuple[int, str]:
    """Upload MP3 to WP Media Library. Returns (media_id, source_url).

    Args:
        mp3_bytes: Raw MP3 content.
        filename:  Filename to register in the Media Library (e.g. ``"recipe.mp3"``).

    Returns:
        Tuple of (media_id, source_url) for the newly created media item.

    Raises:
        httpx.HTTPStatusError: On non-2xx HTTP response.
    """
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "audio/mpeg",
    }
    with wp_client() as client:
        resp = client.post("/wp-json/wp/v2/media", content=mp3_bytes, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    media_id = int(data["id"])
    source_url = str(data["source_url"])
    logger.info(
        "Uploaded audio %r (%d bytes) → %s", filename, len(mp3_bytes), source_url
    )
    return media_id, source_url


def inject_audio_player(post_id: int, media_id: int, source_url: str) -> bool:
    """Insert audio block after first paragraph in post content. Idempotent.

    Args:
        post_id:    WordPress post ID.
        media_id:   WP media ID of the uploaded MP3.
        source_url: Public URL of the uploaded MP3.

    Returns:
        ``True`` if injected, ``False`` if marker already present.

    Raises:
        ValueError: If the post is not found or not published.
        httpx.HTTPStatusError: On non-2xx HTTP response.
    """
    post = fetch_post_data(post_id)
    content: str = post["content"]
    if AUDIO_MARKER in content:
        logger.info("Post %d already has audio player — skipping injection.", post_id)
        return False
    block = (
        f"\n{AUDIO_MARKER}\n"
        f'<div style="width:100%;max-width:100%;box-sizing:border-box">\n'
        f'<p style="margin-bottom:6px;font-weight:600">🎵 Play it while you cook!</p>\n'
        f'<!-- wp:audio {{"id":{media_id}}} -->\n'
        f'<figure class="wp-block-audio" style="width:100% !important;max-width:100% !important;margin:0 !important;padding:0 !important">'
        f'<audio controls src="{source_url}" style="width:100% !important;display:block"></audio></figure>\n'
        f'<!-- /wp:audio -->\n'
        f'</div>\n'
    )
    if PLACEHOLDER_MARKER in content:
        # Replace the "song coming soon" placeholder slot with the real player.
        updated = re.sub(
            re.escape(PLACEHOLDER_MARKER)
            + r'\s*<div class="dff-song-placeholder">.*?</div>',
            block.strip(),
            content,
            count=1,
            flags=re.S,
        )
    else:
        idx = content.find("</p>")
        if idx == -1:
            updated = block + content
        else:
            updated = content[: idx + 4] + block + content[idx + 4 :]
    with wp_client() as client:
        resp = client.patch(
            f"/wp-json/wp/v2/posts/{post_id}", json={"content": updated}
        )
    resp.raise_for_status()
    logger.info("Injected audio player into post %d → %s", post_id, source_url)
    return True
