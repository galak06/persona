"""What is in this reference image? -- the upload-time vision pass.

The library holds any photo that grounds generated imagery: the brand's
mascot, but equally ingredients, a kitchen, a product, a location, a style
plate. Asking the operator to tag each one was busywork, so every upload is
described and tagged here instead, and the operator edits the result
afterwards (`PATCH .../images/{id}`) rather than up front.

Three answers come back per image:

* `description` -- one short sentence about what is ACTUALLY in the frame,
  stored on the manifest entry so downstream generators can read it.
* `category` -- reused from the brand's existing tags whenever one fits, and
  otherwise a NEW short label the model proposes, which the caller then
  creates. `is_new_category` says which of the two happened, and it is
  computed HERE, from the caller's own list -- never taken from the model,
  which has every incentive to claim its invention was on the list.
* `shows_mascot` -- per image, because "the mascot appears in this photo" is
  a property of the photo, not of the tag it happens to carry.

`analyze_image` is the model call; `analyze_for_brand` is the seam routes
use, which fills in the brand's own tag list and mascot name.

This is still an ADVISORY pass, not a content gate: nothing here decides
whether the photo belongs in the library, because the person uploading it
already did. So every failure path returns `None`, never an exception -- a
missing API key, a timeout, a 500, a malformed body, a refusal -- and the
caller falls back to filing the image untagged. An upload must never fail
because the vision model was unavailable.

`lib.gemini_client` is text-only, so the request is built here in the shape
`lib.crew.wp_image._call_nano_pro` uses (an `inline_data` part before the
text part) -- but authenticated with the `x-goog-api-key` HEADER rather than
a `?key=` query param, which would leak the key into every access log.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from lib.crew.reference_library import list_category_labels
from lib.observability import get_logger

logger = get_logger(__name__)

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_DEFAULT_MODEL = "gemini-3.6-flash"
_TIMEOUT_SEC = 20.0

#: Where an image lands when the model answered but named no category at all.
#: Not "new": `general` is the library's own catch-all, so proposing it as a
#: fresh tag would be noise.
FALLBACK_CATEGORY = "general"

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "description": {"type": "STRING"},
        "category": {"type": "STRING"},
        "shows_mascot": {"type": "BOOLEAN"},
    },
    "required": ["description", "category", "shows_mascot"],
}


@dataclass(frozen=True)
class ImageAnalysis:
    """One image, as the model sees it."""

    description: str  # one short sentence: what is actually in the image
    category: str  # a label -- reused from existing_categories, or newly proposed
    shows_mascot: bool  # does the brand's own mascot appear in this image?
    is_new_category: bool  # True when `category` was not in existing_categories


def vision_model() -> str:
    """The model used for tagging. Read per call so it stays overridable."""
    return os.getenv("GEMINI_VISION_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def analyze_image(
    image_bytes: bytes,
    content_type: str,
    *,
    existing_categories: Sequence[str],
    mascot_name: str = "",
) -> ImageAnalysis | None:
    """Describe and tag one image, or `None` if the model could not be asked.

    Args:
        image_bytes: The uploaded image, already validated by
            `reference_validate`.
        content_type: Its SNIFFED mime type (`image/png`, `image/jpeg`, ...).
        existing_categories: The brand's existing category LABELS, in display
            order. May be empty -- a brand with no tags yet gets every label
            proposed fresh.
        mascot_name: The brand's mascot, when it has one. Only sharpens the
            `shows_mascot` question; an empty name still asks it, in terms of
            "the brand's own mascot".

    Returns:
        An `ImageAnalysis`, or `None` for a missing key and every
        transport/HTTP/parse failure. `None` means "file it untagged", not
        "reject the upload".
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        logger.warning("reference_vision_no_api_key")
        return None

    labels = [str(label).strip() for label in existing_categories if str(label).strip()]
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": content_type or "image/png",
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                    {"text": _prompt(labels, mascot_name.strip())},
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
        },
    }

    try:
        response = httpx.post(
            _ENDPOINT.format(model=vision_model()),
            headers={"x-goog-api-key": key},
            json=payload,
            timeout=_TIMEOUT_SEC,
        )
    except httpx.HTTPError as exc:
        logger.warning("reference_vision_request_failed", error=str(exc))
        return None
    if response.status_code >= 400:
        logger.warning("reference_vision_http_error", status=response.status_code)
        return None

    answer = _answer(response)
    return None if answer is None else _analysis(answer, labels)


def analyze_for_brand(
    brand_dir: Path, image_bytes: bytes, content_type: str
) -> ImageAnalysis | None:
    """`analyze_image` for one brand: its own tags, its own mascot name.

    The seam every route calls, so "which categories does this brand have"
    and "what is its mascot called" are answered in one place rather than at
    each call site. Blocking (two small file reads plus the HTTP round trip),
    so callers push it off the event loop.

    Total by construction: this catches everything `analyze_image` does not,
    because an upload must never fail on account of the advisory pass.
    """
    try:
        return analyze_image(
            image_bytes,
            content_type,
            existing_categories=list_category_labels(brand_dir),
            mascot_name=brand_mascot_name(brand_dir),
        )
    except Exception as exc:  # advisory call: no failure of it is worth an error
        logger.warning("reference_vision_analysis_failed", error=str(exc))
        return None


def brand_mascot_name(brand_dir: Path) -> str:
    """`site.mascot_name` from the brand config, or `""`.

    Defensive to the point of indifference (the same read as
    `scripts.crewai_social_posts_pipeline._site_identity`): a brand with no
    config, an unreadable one, or one that never named a mascot simply gets
    asked the unnamed version of the mascot question.
    """
    try:
        config = json.loads((brand_dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("reference_vision_brand_config_unreadable", error=str(exc))
        return ""
    site = config.get("site") if isinstance(config, dict) else None
    return str(site.get("mascot_name") or "").strip() if isinstance(site, dict) else ""


def _prompt(labels: list[str], mascot_name: str) -> str:
    """Instruction half of the request -- the image is the other half."""
    mascot = (
        f'the brand\'s own mascot -- a specific dog named "{mascot_name}"'
        if mascot_name
        else "the brand's own mascot (its own pet)"
    )
    options = ", ".join(f'"{label}"' for label in labels) if labels else "(no tags yet)"
    return (
        "You are tagging a photo for a brand's reference-image library. These photos "
        "ground generated imagery, so they show anything the brand shoots: its mascot, "
        "but also ingredients, kitchens, products, locations, and style shots. "
        "Answer three questions about the attached image. "
        "(1) description: one short sentence saying what is actually in the image. "
        f"(2) shows_mascot: true ONLY if {mascot} actually appears in this image. It is "
        "false for any other animal, false for a stock or generic dog that is not that "
        "specific animal, and false for an image with no animal in it at all. "
        f"(3) category: the ONE tag that best fits the image, from this list: {options}. "
        "Prefer reusing a tag from the list over inventing a near-duplicate of one. Only "
        "if none of them fits, propose a NEW tag of your own: at most two words, "
        "lowercase, naming what the photo shows. "
        'Reply as JSON: {"description": "...", "category": "...", "shows_mascot": true}.'
    )


def _answer(response: httpx.Response) -> dict[str, Any] | None:
    """The model's answer object, or `None` for anything unparseable."""
    try:
        data = response.json()
    except ValueError as exc:
        logger.warning("reference_vision_body_not_json", error=str(exc))
        return None
    if not isinstance(data, dict):
        return None
    candidates = data.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        logger.warning("reference_vision_no_candidates")
        return None
    first = candidates[0] if isinstance(candidates[0], dict) else {}
    parts = (first.get("content") or {}).get("parts") or []
    text = ""
    for part in parts if isinstance(parts, list) else []:
        if isinstance(part, dict) and (part.get("text") or "").strip():
            text = str(part["text"]).strip()
            break
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("reference_vision_answer_not_json", text=text[:120])
        return None
    return parsed if isinstance(parsed, dict) else None


def _analysis(answer: dict[str, Any], labels: list[str]) -> ImageAnalysis:
    """Coerce one answer object, deciding `is_new_category` from `labels`.

    The novelty of a label is decided here rather than asked for, so a model
    that mislabels its own invention as "existing" still cannot slip a tag
    past the caller's create-it step.
    """
    description = str(answer.get("description") or "").strip()
    shows_mascot = bool(answer.get("shows_mascot"))
    category = str(answer.get("category") or "").strip()
    if not category:
        logger.warning("reference_vision_no_category")
        return ImageAnalysis(description, FALLBACK_CATEGORY, shows_mascot, False)

    wanted = category.casefold()
    for label in labels:
        if label.casefold() == wanted:
            # The caller's spelling wins, so an answer of "eating" files under
            # their "Eating" instead of declaring a second, lookalike tag.
            return ImageAnalysis(description, label, shows_mascot, False)
    return ImageAnalysis(description, category, shows_mascot, True)
