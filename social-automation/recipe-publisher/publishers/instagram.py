"""Instagram Graph API publisher.

Three entry points, all following the same two-step pattern:
  1. POST /{ig_user_id}/media to create a container (image / carousel / reels)
  2. POST /{ig_user_id}/media_publish with creation_id=container_id

- `publish_to_instagram`       — single image (needs a public image_url)
- `publish_carousel_to_instagram` — 2-10 slides uploaded to WP then stitched
- `publish_reel_to_instagram`    — 9:16 mp4 uploaded to WP then published as REELS

Token refresh is attempted once on 190 (OAuthException).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from generators.image import GeneratedImage
from generators.recipe import Recipe
from publishers.wordpress import (
    upload_image_to_media_library,
    upload_video_to_media_library,
)

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.facebook.com/v23.0"
_MAX_CONTAINER_POLLS = 15
_POLL_INTERVAL_SEC = 2.0
# Reels need longer — Meta's video ingest + transcode can take minutes.
_MAX_REEL_POLLS = 60
_REEL_POLL_INTERVAL_SEC = 5.0


@dataclass
class IGPublishResult:
    media_id: str
    permalink: str | None
    warnings: list[str] = field(default_factory=list)


class InstagramError(RuntimeError):
    pass


def publish_to_instagram(recipe: Recipe, *, image_url: str) -> IGPublishResult:
    # Accept both the recipe-publisher original names and the social-automation
    # project convention (IG_ACCOUNT_ID + FB_PAGE_TOKEN per CLAUDE.md — IG uses
    # the same Page token as Facebook). Project convention takes precedence.
    ig_user_id = os.environ.get("IG_ACCOUNT_ID") or os.environ["IG_USER_ID"]
    token = (
        os.environ.get("FB_PAGE_TOKEN")
        or os.environ.get("IG_GRAPH_ACCESS_TOKEN")
        or ""
    )
    if not token:
        raise InstagramError("FB_PAGE_TOKEN / IG_GRAPH_ACCESS_TOKEN not set")
    warnings: list[str] = []

    with httpx.Client(timeout=60.0, base_url=_GRAPH_BASE) as client:
        try:
            container_id = _create_container(client, ig_user_id, image_url, recipe, token)
        except _TokenExpired:
            token = _refresh_token(token, warnings)
            container_id = _create_container(client, ig_user_id, image_url, recipe, token)

        _wait_for_container(client, container_id, token, warnings)
        media_id = _publish_container(client, ig_user_id, container_id, token)
        permalink = _fetch_permalink(client, media_id, token, warnings)

    return IGPublishResult(media_id=media_id, permalink=permalink, warnings=warnings)


def publish_carousel_to_instagram(
    recipe: Recipe,
    slides: list[GeneratedImage],
) -> IGPublishResult:
    """Upload slides to WP, build N child + 1 parent IG containers, publish.

    Each slide must have `.bytes_` populated. Slides are uploaded to the WP
    media library (not attached to any post) purely so Meta has a public URL
    for container creation. Carousel caption comes from recipe.ig_caption.
    """
    if len(slides) < 2 or len(slides) > 10:
        raise InstagramError(f"carousel requires 2-10 slides, got {len(slides)}")

    ig_user_id = os.environ.get("IG_ACCOUNT_ID") or os.environ["IG_USER_ID"]
    token = (
        os.environ.get("FB_PAGE_TOKEN")
        or os.environ.get("IG_GRAPH_ACCESS_TOKEN")
        or ""
    )
    if not token:
        raise InstagramError("FB_PAGE_TOKEN / IG_GRAPH_ACCESS_TOKEN not set")

    warnings: list[str] = []

    logger.info("uploading %d carousel slides to WP media library", len(slides))
    image_urls: list[str] = []
    for i, img in enumerate(slides, 1):
        filename = f"{recipe.slug}-slide-{i:02d}.jpg"
        _, src = upload_image_to_media_library(img, filename=filename)
        image_urls.append(src)

    with httpx.Client(timeout=90.0, base_url=_GRAPH_BASE) as client:
        child_ids: list[str] = []
        for i, src in enumerate(image_urls, 1):
            resp = client.post(
                f"/{ig_user_id}/media",
                params={
                    "image_url": src,
                    "is_carousel_item": "true",
                    "access_token": token,
                },
            )
            if resp.status_code >= 400:
                raise InstagramError(
                    f"carousel child #{i} create failed: {resp.status_code} {resp.text[:300]}"
                )
            child_ids.append(resp.json()["id"])

        for cid in child_ids:
            _wait_for_container(client, cid, token, warnings)

        parent_resp = client.post(
            f"/{ig_user_id}/media",
            params={
                "media_type": "CAROUSEL",
                "children": ",".join(child_ids),
                "caption": recipe.ig_caption,
                "access_token": token,
            },
        )
        if parent_resp.status_code >= 400:
            raise InstagramError(
                f"carousel parent create failed: {parent_resp.status_code} {parent_resp.text[:300]}"
            )
        parent_id = parent_resp.json()["id"]
        _wait_for_container(client, parent_id, token, warnings)

        pub_resp = client.post(
            f"/{ig_user_id}/media_publish",
            params={"creation_id": parent_id, "access_token": token},
        )
        if pub_resp.status_code >= 400:
            raise InstagramError(
                f"carousel media_publish failed: {pub_resp.status_code} {pub_resp.text[:300]}"
            )
        media_id = pub_resp.json()["id"]
        permalink = _fetch_permalink(client, media_id, token, warnings)

    return IGPublishResult(media_id=media_id, permalink=permalink, warnings=warnings)


def publish_reel_to_instagram(
    recipe: Recipe,
    video_path: Path,
) -> IGPublishResult:
    """Upload an mp4 to WP, create a REELS container, publish it.

    `video_path` must point to a 9:16 H.264 mp4 composed by generators.reel.
    Caption comes from recipe.ig_caption. Reels processing can take minutes
    on Meta's side, so the container poll budget is ~5 minutes.
    """
    if not video_path.exists():
        raise InstagramError(f"video file not found: {video_path}")

    ig_user_id = os.environ.get("IG_ACCOUNT_ID") or os.environ["IG_USER_ID"]
    token = (
        os.environ.get("FB_PAGE_TOKEN")
        or os.environ.get("IG_GRAPH_ACCESS_TOKEN")
        or ""
    )
    if not token:
        raise InstagramError("FB_PAGE_TOKEN / IG_GRAPH_ACCESS_TOKEN not set")

    warnings: list[str] = []

    filename = f"{recipe.slug}-reel.mp4"
    logger.info("uploading reel video to WP media library: %s", filename)
    _, video_url = upload_video_to_media_library(video_path, filename=filename)

    with httpx.Client(timeout=120.0, base_url=_GRAPH_BASE) as client:
        resp = client.post(
            f"/{ig_user_id}/media",
            params={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": recipe.ig_caption,
                "access_token": token,
            },
        )
        if resp.status_code == 400 and _is_oauth_error(resp.json()):
            token = _refresh_token(token, warnings)
            resp = client.post(
                f"/{ig_user_id}/media",
                params={
                    "media_type": "REELS",
                    "video_url": video_url,
                    "caption": recipe.ig_caption,
                    "access_token": token,
                },
            )
        if resp.status_code >= 400:
            raise InstagramError(
                f"reel container create failed: {resp.status_code} {resp.text[:300]}"
            )
        container_id = resp.json()["id"]

        _wait_for_container(
            client,
            container_id,
            token,
            warnings,
            max_polls=_MAX_REEL_POLLS,
            poll_interval_sec=_REEL_POLL_INTERVAL_SEC,
        )
        media_id = _publish_container(client, ig_user_id, container_id, token)
        permalink = _fetch_permalink(client, media_id, token, warnings)

    return IGPublishResult(media_id=media_id, permalink=permalink, warnings=warnings)


# ---------- internals ----------


class _TokenExpired(Exception):
    pass


def _create_container(
    client: httpx.Client,
    ig_user_id: str,
    image_url: str,
    recipe: Recipe,
    token: str,
) -> str:
    resp = client.post(
        f"/{ig_user_id}/media",
        params={
            "image_url": image_url,
            "caption": recipe.ig_caption,
            "access_token": token,
        },
    )
    if resp.status_code == 400 and _is_oauth_error(resp.json()):
        raise _TokenExpired()
    if resp.status_code >= 400:
        raise InstagramError(f"container create failed: {resp.status_code} {resp.text}")
    return resp.json()["id"]


def _wait_for_container(
    client: httpx.Client,
    container_id: str,
    token: str,
    warnings: list[str],
    *,
    max_polls: int = _MAX_CONTAINER_POLLS,
    poll_interval_sec: float = _POLL_INTERVAL_SEC,
) -> None:
    for _ in range(max_polls):
        r = client.get(
            f"/{container_id}",
            params={"fields": "status_code", "access_token": token},
        )
        r.raise_for_status()
        status = r.json().get("status_code")
        if status == "FINISHED":
            return
        if status in {"ERROR", "EXPIRED"}:
            raise InstagramError(f"container status={status}")
        time.sleep(poll_interval_sec)
    warnings.append(
        f"container {container_id} never reached FINISHED within "
        f"{max_polls * poll_interval_sec}s — publishing anyway"
    )


def _publish_container(
    client: httpx.Client, ig_user_id: str, container_id: str, token: str
) -> str:
    resp = client.post(
        f"/{ig_user_id}/media_publish",
        params={"creation_id": container_id, "access_token": token},
    )
    if resp.status_code >= 400:
        raise InstagramError(f"media_publish failed: {resp.status_code} {resp.text}")
    return resp.json()["id"]


def _fetch_permalink(
    client: httpx.Client, media_id: str, token: str, warnings: list[str]
) -> str | None:
    r = client.get(
        f"/{media_id}",
        params={"fields": "permalink", "access_token": token},
    )
    if r.status_code >= 400:
        warnings.append(f"failed to fetch permalink for media_id={media_id}")
        return None
    return r.json().get("permalink")


def _is_oauth_error(body: dict) -> bool:
    err = body.get("error", {})
    return err.get("code") == 190 or err.get("type") == "OAuthException"


def _refresh_token(current: str, warnings: list[str]) -> str:
    """Try to exchange a short-lived token for a long-lived one. Returns refreshed token."""
    app_id = os.getenv("FB_APP_ID")
    app_secret = os.getenv("FB_APP_SECRET")
    if not app_id or not app_secret:
        raise InstagramError(
            "IG token expired and FB_APP_ID/FB_APP_SECRET not set for refresh"
        )
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            f"{_GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": current,
            },
        )
        if r.status_code >= 400:
            raise InstagramError(f"token refresh failed: {r.status_code} {r.text}")
        new_token = r.json()["access_token"]
    warnings.append("IG token refreshed; rotate IG_GRAPH_ACCESS_TOKEN in secrets store")
    return new_token
