"""Category suggestion for a mascot reference upload -- convenience only.

One job: the operator has already picked a photo they intend to keep, and
this saves them the click of choosing which of their own tags it belongs to.
It is deliberately NOT an identity gate -- nothing here decides whether the
photo shows the right dog, because the person uploading it already did.

Consequences of "convenience only":

* Every failure path returns `None`, never an exception. A missing API key, a
  timeout, a 500, a malformed body, a refusal -- all of them mean "the
  selector stays empty", which is exactly the pre-existing behaviour.
* A label the model invented is discarded. The answer must appear VERBATIM in
  the caller's list, so a suggestion can never create a category or silently
  file a photo under a tag the brand never declared.
* No categories in, `None` out, with no network call at all.

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

import httpx

from lib.observability import get_logger

logger = get_logger(__name__)

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_DEFAULT_MODEL = "gemini-3.6-flash"
_TIMEOUT_SEC = 20.0

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"category": {"type": "STRING"}},
    "required": ["category"],
}


def vision_model() -> str:
    """The model used for tagging. Read per call so it stays overridable."""
    return os.getenv("GEMINI_VISION_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def suggest_category(
    image_bytes: bytes, content_type: str, categories: Sequence[str]
) -> str | None:
    """Best-matching label from `categories` for this photo, or `None`.

    Args:
        image_bytes: The uploaded image, already validated by `mascot_validate`.
        content_type: Its SNIFFED mime type (`image/png`, `image/jpeg`, ...).
        categories: The brand's existing category LABELS, in display order.

    Returns:
        One element of `categories`, matched case-insensitively and returned
        exactly as the caller spelled it, or `None` -- which covers an empty
        list, a missing key, any transport/HTTP/parse failure, an explicit
        "none of these", and a label the model made up.
    """
    labels = [str(label).strip() for label in categories if str(label).strip()]
    if not labels:
        return None

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        logger.warning("mascot_vision_no_api_key")
        return None

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
                    {"text": _prompt(labels)},
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
        logger.warning("mascot_vision_request_failed", error=str(exc))
        return None
    if response.status_code >= 400:
        logger.warning("mascot_vision_http_error", status=response.status_code)
        return None

    return _match(_answer(response), labels)


def _prompt(labels: list[str]) -> str:
    """Instruction half of the request -- the photo is the other half."""
    options = ", ".join(f'"{label}"' for label in labels)
    return (
        "You are tagging a photo for a brand's reference-image library. "
        f"Choose the ONE tag that best describes what the photo shows, from exactly this list: "
        f'{options}. Reply as JSON: {{"category": "<one tag, copied verbatim>"}}. '
        'If none of the tags fit the photo, reply {"category": ""}. '
        "Never invent a tag that is not in the list."
    )


def _answer(response: httpx.Response) -> str:
    """The model's `category` string, or `""` for anything unparseable."""
    try:
        data = response.json()
    except ValueError as exc:
        logger.warning("mascot_vision_body_not_json", error=str(exc))
        return ""
    if not isinstance(data, dict):
        return ""
    candidates = data.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        logger.warning("mascot_vision_no_candidates")
        return ""
    first = candidates[0] if isinstance(candidates[0], dict) else {}
    parts = (first.get("content") or {}).get("parts") or []
    text = ""
    for part in parts if isinstance(parts, list) else []:
        if isinstance(part, dict) and (part.get("text") or "").strip():
            text = str(part["text"]).strip()
            break
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("mascot_vision_answer_not_json", text=text[:120])
        return ""
    return str(parsed.get("category") or "") if isinstance(parsed, dict) else ""


def _match(answer: str, labels: list[str]) -> str | None:
    """The caller's spelling of `answer`, or `None` if it is not in the list."""
    wanted = answer.strip().casefold()
    if not wanted:
        return None
    for label in labels:
        if label.casefold() == wanted:
            return label
    logger.warning("mascot_vision_label_not_offered", answer=answer[:60])
    return None
