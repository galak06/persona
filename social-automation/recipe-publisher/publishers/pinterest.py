"""Pinterest API v5 publisher.

Creates one Pin per carousel slide (default 4), all linking to the recipe's
WordPress post URL. Mirrors the Instagram publisher pattern so it drops into
the same orchestrator step right after `publish_carousel_to_instagram`.

Token refresh logic lives in pinterest_auth.py.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

import httpx

from generators.image import GeneratedImage
from generators.recipe import Recipe
from publishers.pinterest_auth import refresh_token as _refresh_token

logger = logging.getLogger(__name__)

_API_BASE = "https://api.pinterest.com/v5"
_PIN_GAP_SEC = 2.5
_MAX_TITLE = 100
_MAX_DESCRIPTION = 500


@dataclass
class PinterestPin:
    pin_id: str
    permalink: str
    slide_index: int


@dataclass
class PinterestPublishResult:
    board_id: str
    pins: list[PinterestPin] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class PinterestError(RuntimeError):
    pass


class PinterestSkipped(RuntimeError):
    """Publishing was skipped on purpose (disabled, or Trial-access blocked).

    Distinct from PinterestError so the orchestrator can log a calm warning
    instead of a full error traceback.
    """


def _is_enabled() -> bool:
    """`PINTEREST_ENABLED` defaults to true; set to false/0/no/off to skip."""
    val = os.environ.get("PINTEREST_ENABLED", "true").strip().lower()
    return val not in {"false", "0", "no", "off", ""}


# ---------- public API ----------


def publish_pins_for_recipe(
    recipe: Recipe,
    slides: list[GeneratedImage],
    *,
    wp_post_url: str,
    slide_urls: list[str] | None = None,
) -> PinterestPublishResult:
    """Create one Pin per slide, all linking to the recipe's WP post URL.

    If slide_urls is supplied (the normal path — the IG step already uploaded
    each slide to the WP media library), reuse those URLs. Otherwise upload
    each slide to WP first.
    """
    if not _is_enabled():
        raise PinterestSkipped(
            "PINTEREST_ENABLED=false — skipping pin creation (Trial-access mode)"
        )
    board_id = os.environ.get("PINTEREST_BOARD_ID")
    token = os.environ.get("PINTEREST_ACCESS_TOKEN")
    if not board_id:
        raise PinterestError("PINTEREST_BOARD_ID not set")
    if not token:
        raise PinterestError("PINTEREST_ACCESS_TOKEN not set")

    if slide_urls is None:
        slide_urls = _upload_slides_to_wp(recipe, slides)
    if len(slide_urls) != len(slides):
        raise PinterestError(
            f"slide_urls length ({len(slide_urls)}) != slides length ({len(slides)})"
        )

    warnings: list[str] = []
    result = PinterestPublishResult(board_id=board_id, warnings=warnings)

    with httpx.Client(timeout=60.0, base_url=_API_BASE) as client:
        for i, (slide, url) in enumerate(zip(slides, slide_urls), start=1):
            body = _build_pin_body(
                recipe=recipe,
                wp_post_url=wp_post_url,
                slide_url=url,
                slide=slide,
                slide_index=i,
                board_id=board_id,
            )
            try:
                pin_id = _create_pin(client, body, token)
            except _TokenExpired:
                token = _refresh_token(warnings)
                pin_id = _create_pin(client, body, token)

            result.pins.append(
                PinterestPin(
                    pin_id=pin_id,
                    permalink=f"https://www.pinterest.com/pin/{pin_id}/",
                    slide_index=i,
                )
            )
            logger.info("pinterest pin %d/%d created: %s", i, len(slides), pin_id)
            if i < len(slides):
                time.sleep(_PIN_GAP_SEC)

    return result


def create_single_pin(
    *,
    image_url: str,
    link: str,
    title: str,
    description: str,
    alt_text: str = "",
    board_id: str | None = None,
) -> PinterestPin:
    """Low-level helper reused by backfill + legacy-fix scripts."""
    if not _is_enabled():
        raise PinterestSkipped(
            "PINTEREST_ENABLED=false — skipping single-pin creation"
        )
    board = board_id or os.environ["PINTEREST_BOARD_ID"]
    token = os.environ["PINTEREST_ACCESS_TOKEN"]
    body = {
        "link": link,
        "title": _truncate(title, _MAX_TITLE),
        "description": _truncate(description, _MAX_DESCRIPTION),
        "board_id": board,
        "alt_text": _truncate(alt_text or title, _MAX_TITLE),
        "media_source": {"source_type": "image_url", "url": image_url},
    }
    warnings: list[str] = []
    with httpx.Client(timeout=60.0, base_url=_API_BASE) as client:
        try:
            pin_id = _create_pin(client, body, token)
        except _TokenExpired:
            token = _refresh_token(warnings)
            pin_id = _create_pin(client, body, token)
    return PinterestPin(
        pin_id=pin_id,
        permalink=f"https://www.pinterest.com/pin/{pin_id}/",
        slide_index=1,
    )


def update_pin(pin_id: str, *, title: str | None = None, description: str | None = None,
               link: str | None = None, alt_text: str | None = None) -> None:
    """PATCH an existing pin. Used by the legacy-pin fixer."""
    token = os.environ["PINTEREST_ACCESS_TOKEN"]
    payload: dict = {}
    if title is not None:
        payload["title"] = _truncate(title, _MAX_TITLE)
    if description is not None:
        payload["description"] = _truncate(description, _MAX_DESCRIPTION)
    if link is not None:
        payload["link"] = link
    if alt_text is not None:
        payload["alt_text"] = _truncate(alt_text, _MAX_TITLE)
    if not payload:
        return
    warnings: list[str] = []
    with httpx.Client(timeout=60.0, base_url=_API_BASE) as client:
        for _ in range(2):
            resp = client.patch(
                f"/pins/{pin_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code == 401:
                token = _refresh_token(warnings)
                continue
            if resp.status_code >= 400:
                raise PinterestError(
                    f"pin update {pin_id} failed: {resp.status_code} {resp.text[:300]}"
                )
            return


# ---------- internals ----------


class _TokenExpired(Exception):
    pass


def _build_pin_body(
    *,
    recipe: Recipe,
    wp_post_url: str,
    slide_url: str,
    slide: GeneratedImage,
    slide_index: int,
    board_id: str,
) -> dict:
    return {
        "link": wp_post_url,
        "title": _truncate(recipe.title, _MAX_TITLE),
        "description": _truncate(
            _compose_description(recipe, slide_index), _MAX_DESCRIPTION
        ),
        "board_id": board_id,
        "alt_text": _truncate(slide.alt_text or recipe.title, _MAX_TITLE),
        "media_source": {"source_type": "image_url", "url": slide_url},
    }


def _compose_description(recipe: Recipe, slide_index: int) -> str:
    base = (recipe.meta_description or "").strip()
    if not base:
        base = (
            f"Homemade {recipe.title.lower()} for dogs — simple, "
            "vet-conscious ingredients."
        )
    # Slot-specific CTA so the four pins don't look identical in feed.
    tails = {
        1: "Full printable recipe on dogfoodandfun.com.",
        2: "Ingredients, portions, and prep notes at dogfoodandfun.com.",
        3: "Step-by-step instructions at dogfoodandfun.com.",
        4: "Save this for your next dog meal prep — recipe at dogfoodandfun.com.",
    }
    tail = tails.get(slide_index, "Full recipe at dogfoodandfun.com.")
    return f"{base}  {tail}"


def _create_pin(client: httpx.Client, body: dict, token: str) -> str:
    resp = client.post(
        "/pins",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
    )
    if resp.status_code == 401:
        raise _TokenExpired()
    if resp.status_code == 403:
        # Trial-access apps return 403 with code 29 on POST /pins. Treat as
        # an expected skip rather than a hard failure so the orchestrator can
        # log a calm warning (real 403s on other endpoints still raise below).
        try:
            code = resp.json().get("code")
        except (ValueError, KeyError):
            code = None
        if code == 29:
            raise PinterestSkipped(
                f"trial-access — POST /pins blocked (code 29): {resp.text[:200]}"
            )
    if resp.status_code >= 400:
        raise PinterestError(
            f"pin create failed: {resp.status_code} {resp.text[:400]}"
        )
    return resp.json()["id"]


def _upload_slides_to_wp(recipe: Recipe, slides: list[GeneratedImage]) -> list[str]:
    from publishers.wordpress import upload_image_to_media_library

    urls: list[str] = []
    for i, slide in enumerate(slides, start=1):
        filename = f"{recipe.slug}-pin-{i:02d}.jpg"
        _, src = upload_image_to_media_library(slide, filename=filename)
        urls.append(src)
    return urls


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


