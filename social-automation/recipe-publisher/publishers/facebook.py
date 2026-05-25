"""Facebook Page publisher — Reels via the Graph API 3-phase upload.

Unlike IG Reels (container → poll → publish), FB Reels need three separate
POSTs to upload + a `video_state=PUBLISHED` on the finish call.

    1. start    POST /{page_id}/video_reels?upload_phase=start
                  → {video_id, upload_url}
    2. transfer POST {upload_url}      (binary body, offset + file_size headers)
                  → {success: true}
    3. finish   POST /{page_id}/video_reels
                  ?video_id=…&upload_phase=finish&video_state=PUBLISHED
                  &description=…&access_token=…
                  → {success: true, post_id}

Token: reuses the same `FB_PAGE_TOKEN` we already use for FB page posts +
IG Reels. Scope `pages_manage_posts` is sufficient.

Reference: https://developers.facebook.com/docs/video-api/guides/reels-publishing/
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from generators.recipe import Recipe

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.facebook.com/v23.0"
_MAX_FINISH_POLLS = 60      # FB transcode up to 5 min
_FINISH_POLL_INTERVAL = 5.0


@dataclass
class FBReelPublishResult:
    video_id: str
    post_id: str | None = None
    permalink: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class FBPagePostResult:
    post_id: str
    permalink: str | None = None
    warnings: list[str] = field(default_factory=list)
    comment_id: str | None = None
    comment_warnings: list[str] = field(default_factory=list)


class FacebookError(RuntimeError):
    pass


def publish_link_post_to_facebook(
    *,
    message: str,
    link: str,
    link_in_first_comment: bool = False,
    dry_run: bool = False,
) -> FBPagePostResult:
    """Post to the FB Page feed.

    Default behavior (link_in_first_comment=False): posts a link card via
    `/feed` with `link=` so FB renders the WP Open Graph card. Cleaner than
    a raw photo because:

    - the card matches WP's OG tags (consistent branding),
    - clicks go directly to WP (better attribution),
    - link cards out-perform photo posts on click-through for link-driven
      content like recipe blogs.

    When `link_in_first_comment=True`: drops the `link` field AND keeps the
    URL out of `message` — body is text-only. Caller is expected to post the
    URL as a follow-up first comment via `post_first_comment_to_facebook`.
    This pattern dodges the FB outbound-link reach penalty on Page posts.

    `message` is the conversational copy. Hashtags allowed but keep it short
    (2-4 sentences) — the card or first comment carries the URL.
    """
    page_id = os.environ.get("FB_PAGE_ID") or ""
    token = os.environ.get("FB_PAGE_TOKEN") or ""
    if not page_id:
        raise FacebookError("FB_PAGE_ID not set")
    if not token:
        raise FacebookError("FB_PAGE_TOKEN not set")

    payload: dict[str, str] = {"message": message, "access_token": token}
    if not link_in_first_comment:
        payload["link"] = link

    if dry_run:
        redacted = {k: ("<redacted>" if k == "access_token" else v) for k, v in payload.items()}
        logger.info(
            "[dry-run] FB /feed POST url=%s/%s/feed payload=%s",
            _GRAPH_BASE, page_id, redacted,
        )
        return FBPagePostResult(post_id="dryrun_post_id", permalink=None, warnings=[])

    warnings: list[str] = []
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{_GRAPH_BASE}/{page_id}/feed", data=payload)
        if resp.status_code >= 400:
            raise FacebookError(f"FB feed post failed: {resp.status_code} {resp.text[:300]}")
        post_id = resp.json().get("id", "")
        if not post_id:
            raise FacebookError(f"FB feed post returned no id: {resp.text[:300]}")

        permalink: str | None = None
        try:
            perm_resp = client.get(
                f"{_GRAPH_BASE}/{post_id}",
                params={"fields": "permalink_url", "access_token": token},
            )
            if perm_resp.status_code == 200:
                permalink = perm_resp.json().get("permalink_url")
            else:
                warnings.append(f"permalink fetch {perm_resp.status_code}")
        except Exception as exc:  # noqa: BLE001 — permalink is cosmetic
            warnings.append(f"permalink fetch error: {exc}")

    logger.info(
        "FB Page feed post published: id=%s permalink=%s link_in_first_comment=%s",
        post_id, permalink, link_in_first_comment,
    )
    return FBPagePostResult(post_id=post_id, permalink=permalink, warnings=warnings)


def post_first_comment_to_facebook(
    post_id: str,
    message: str,
    *,
    access_token: str | None = None,
    dry_run: bool = False,
) -> tuple[str | None, list[str]]:
    """Post a first comment on the page post (best-effort).

    Used right after `publish_link_post_to_facebook` with
    `link_in_first_comment=True` to drop the WP URL as a comment instead of
    embedding it in the body — sidesteps FB's outbound-link reach penalty.

    Returns `(comment_id_or_None, warnings)`. Never raises: the page post
    itself already succeeded, the comment is cosmetic. Caller can fold the
    warnings into their existing warnings surface.
    """
    token = access_token or os.environ.get("FB_PAGE_TOKEN") or ""
    if not token:
        return None, ["post_first_comment_to_facebook: FB_PAGE_TOKEN not set"]

    payload: dict[str, str] = {"message": message, "access_token": token}

    if dry_run:
        redacted = {k: ("<redacted>" if k == "access_token" else v) for k, v in payload.items()}
        logger.info(
            "[dry-run] FB /{post_id}/comments POST url=%s/%s/comments payload=%s",
            _GRAPH_BASE, post_id, redacted,
        )
        return "dryrun_comment_id", []

    warnings: list[str] = []
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{_GRAPH_BASE}/{post_id}/comments", data=payload)
            if resp.status_code >= 400:
                msg = (
                    f"first-comment POST failed: {resp.status_code} "
                    f"{resp.text[:300]}"
                )
                logger.warning("FB %s post_id=%s", msg, post_id)
                warnings.append(msg)
                return None, warnings
            body = resp.json()
            comment_id = body.get("id")
            if not comment_id:
                msg = f"first-comment POST returned no id: {resp.text[:300]}"
                logger.warning("FB %s post_id=%s", msg, post_id)
                warnings.append(msg)
                return None, warnings
            logger.info(
                "FB Page first-comment posted: post_id=%s comment_id=%s",
                post_id, comment_id,
            )
            return comment_id, warnings
    except httpx.HTTPError as exc:
        msg = f"first-comment POST httpx error: {exc}"
        logger.warning("FB %s post_id=%s", msg, post_id)
        return None, [msg]


def publish_reel_to_facebook(
    recipe: Recipe,
    video_path: Path,
    *,
    description: str | None = None,
) -> FBReelPublishResult:
    """Upload + publish an mp4 as a Reel on the Facebook Page.

    If `description` is None we fall back to `recipe.ig_caption` — the caption
    is already brand-validated, which is more conservative than deriving a
    separate FB variant. Caller can pass a custom description for campaigns
    that want an Amazon affiliate URL in the body (FB lets you include raw
    links in Reel descriptions, unlike IG).
    """
    if not video_path.exists():
        raise FacebookError(f"video file not found: {video_path}")

    page_id = os.environ.get("FB_PAGE_ID") or ""
    token = os.environ.get("FB_PAGE_TOKEN") or ""
    if not page_id:
        raise FacebookError("FB_PAGE_ID not set")
    if not token:
        raise FacebookError("FB_PAGE_TOKEN not set")

    caption = description if description is not None else recipe.ig_caption
    warnings: list[str] = []

    with httpx.Client(timeout=300.0) as client:
        video_id, upload_url = _phase_start(client, page_id, token)
        _phase_transfer(client, upload_url, token, video_path)
        post_id = _phase_finish(client, page_id, token, video_id, caption, warnings)
        permalink = _fetch_permalink(client, post_id, token, warnings) if post_id else None

    return FBReelPublishResult(
        video_id=video_id,
        post_id=post_id,
        permalink=permalink,
        warnings=warnings,
    )


# ---------- internals ----------


def _phase_start(client: httpx.Client, page_id: str, token: str) -> tuple[str, str]:
    resp = client.post(
        f"{_GRAPH_BASE}/{page_id}/video_reels",
        params={"upload_phase": "start", "access_token": token},
    )
    if resp.status_code >= 400:
        raise FacebookError(f"reel start failed: {resp.status_code} {resp.text[:300]}")
    body = resp.json()
    video_id = body.get("video_id")
    upload_url = body.get("upload_url")
    if not video_id or not upload_url:
        raise FacebookError(f"reel start missing fields: {body!r}")
    logger.info("FB reel start: video_id=%s", video_id)
    return video_id, upload_url


def _phase_transfer(
    client: httpx.Client, upload_url: str, token: str, video_path: Path
) -> None:
    data = video_path.read_bytes()
    headers = {
        "Authorization": f"OAuth {token}",
        "offset": "0",
        "file_size": str(len(data)),
    }
    resp = client.post(upload_url, headers=headers, content=data)
    if resp.status_code >= 400:
        raise FacebookError(
            f"reel transfer failed: {resp.status_code} {resp.text[:300]}"
        )
    if not resp.json().get("success"):
        raise FacebookError(f"reel transfer returned non-success: {resp.text[:300]}")
    logger.info("FB reel transfer OK: %d bytes", len(data))


def _phase_finish(
    client: httpx.Client,
    page_id: str,
    token: str,
    video_id: str,
    description: str,
    warnings: list[str],
) -> str | None:
    resp = client.post(
        f"{_GRAPH_BASE}/{page_id}/video_reels",
        params={
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": description,
            "access_token": token,
        },
    )
    if resp.status_code >= 400:
        raise FacebookError(
            f"reel finish failed: {resp.status_code} {resp.text[:300]}"
        )
    body = resp.json()
    if not body.get("success"):
        raise FacebookError(f"reel finish non-success: {body!r}")

    # Post id isn't always returned synchronously — poll status to get it.
    for _ in range(_MAX_FINISH_POLLS):
        status = client.get(
            f"{_GRAPH_BASE}/{video_id}",
            params={"fields": "status,post_id", "access_token": token},
        )
        if status.status_code < 400:
            data = status.json()
            processing = data.get("status", {}).get("video_status", "")
            if processing == "ready":
                return data.get("post_id")
            if processing in {"error", "expired"}:
                raise FacebookError(f"reel processing ended {processing}: {data!r}")
        time.sleep(_FINISH_POLL_INTERVAL)

    warnings.append(
        f"reel {video_id} never reported ready within "
        f"{_MAX_FINISH_POLLS * _FINISH_POLL_INTERVAL}s — continuing"
    )
    return None


def _fetch_permalink(
    client: httpx.Client, post_id: str, token: str, warnings: list[str]
) -> str | None:
    r = client.get(
        f"{_GRAPH_BASE}/{post_id}",
        params={"fields": "permalink_url", "access_token": token},
    )
    if r.status_code >= 400:
        warnings.append(f"failed to fetch permalink for post_id={post_id}")
        return None
    return r.json().get("permalink_url")
