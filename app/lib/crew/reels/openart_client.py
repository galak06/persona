"""OpenArt MCP client: turn one `ReelBeat.image_prompt` string into a
downloaded image, one call per beat (5 total for a full reel).

Uses `lib.oauth.openart.build_openart_oauth_provider()` for auth -- the same
provider drives the one-time interactive consent flow (if no token is
stored yet) or transparently refreshes a stored token, so this module never
handles credentials itself.

**Real OpenArt tool contract (confirmed live).** `openart_model_form_get(model,
mode)` returns a flat `defaults` dict for that model+mode's dynamic
parameter schema -- the one part of the response that's consistent across
models regardless of schema complexity; `_build_image_params` maps our
prompt text and Reels-specific overrides (vertical aspect ratio, reference
image) onto it, rather than assuming a fixed shape. `_DEFAULT_MODEL` is a
single hardcoded choice (`nano-banana-2-lite`) -- OpenArt's own catalog
describes it as "fastest, most cost-efficient... pick when speed or cost
matters more than maximum quality," matching why this path replaced
OpenArt's (far more expensive) video generation in the first place.

**Two modes, chosen per call.** `text2image` (no reference) is a pure
prompt -> image call with nothing to ground the subject's actual
appearance. `image2image` additionally takes a `visualReferences` array
(confirmed live shape: `{type:"image", id, url, label}`, exactly what
`openart_upload_sign` returns) -- passing the WP post's own hero image
here keeps the generated dog visually consistent with the brand's actual
mascot instead of OpenArt inventing a generic one, which is why
`generate_image` accepts an optional `reference_image` and switches mode
automatically when one is given. Uploading a reference is itself a
confirmed-live 2-step flow: `openart_upload_sign` returns a `signURL` (a
GCS resumable-upload URL) and the `visualReference` object to use later;
the raw bytes get PUT to `signURL` directly (not through the MCP tool
layer), then `openart_upload_list` is polled briefly until the upload
shows up (confirmed live: ready within ~2s for a ~660KB JPEG).

`openart_generate_image`'s call shape (sync vs. async `historyId` + poll,
same as confirmed for `openart_generate_video`) is confirmed live too --
`generate_image` below handles both: it checks the initial call's own
result for an image URL first, and only falls into the
`openart_creation_wait` poll loop if a `historyId` shows up instead.

Raises on any failure -- auth expiry, unknown tool, tool error, insufficient
credits, timeout, network error -- all propagate identically. Deliberately
does **not** try to distinguish credit-exhaustion from other failures: the
orchestrator (`scripts/crewai_reels_pipeline.py`) catches broadly and falls
back to the deterministic (reused hero image) path regardless of cause.
"""

from __future__ import annotations

import json

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, Tool

from lib.oauth.openart import OPENART_MCP_URL, build_openart_oauth_provider
from lib.observability import get_logger

logger = get_logger(__name__)

_PROMPT_FIELD_CANDIDATES = ("prompt", "instructions", "text", "description")
_REFERENCE_FIELD_CANDIDATES = ("visualReferences", "referenceImages", "images")

_DEFAULT_MODEL = "nano-banana-2-lite"
_TEXT_TO_IMAGE_MODE = "text2image"
_IMAGE_TO_IMAGE_MODE = "image2image"
_TARGET_ASPECT_RATIO = "9:16"

_REQUIRED_TOOL_NAMES = (
    "openart_model_form_get",
    "openart_generate_image",
    "openart_creation_wait",
    "openart_upload_sign",
    "openart_upload_list",
)
_POLL_TIMEOUT_SECONDS = 90
_MAX_POLL_ATTEMPTS = 60  # confirmed live: a real generation outlived a 12-attempt (~70s) budget
_DEFAULT_POLL_AFTER_SECONDS = 5.0
_UPLOAD_READY_POLL_ATTEMPTS = 6
_UPLOAD_READY_POLL_SECONDS = 2.0


class OpenArtToolError(RuntimeError):
    """Raised when OpenArt's MCP server is missing an expected tool, a call
    to it errors, or its result doesn't yield the data the next step needs."""


def _ensure_tools_available(tools: list[Tool]) -> None:
    names = {tool.name for tool in tools}
    missing = [name for name in _REQUIRED_TOOL_NAMES if name not in names]
    if missing:
        raise OpenArtToolError(
            f"OpenArt's MCP server is missing expected tool(s) {missing}; "
            f"it currently exposes: {sorted(names)}"
        )


def _result_payload(result: CallToolResult) -> dict[str, object]:
    """Pull a JSON object out of a tool result -- checks `structuredContent`
    first, then falls back to parsing the first text content block as JSON
    (several OpenArt tools return data as text only, no output schema)."""
    if result.isError:
        raise OpenArtToolError(f"OpenArt tool call returned an error: {result.content!r}")

    if result.structuredContent:
        return result.structuredContent

    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                payload = json.loads(text)
            except ValueError:
                continue
            if isinstance(payload, dict):
                return payload

    return {}


def _extract_form_defaults(result: CallToolResult) -> dict[str, object]:
    """`openart_model_form_get`'s per-model/mode parameter schema varies, but
    every model returns a flat `defaults` dict covering every field that
    schema declares -- the one consistently-shaped part of the response."""
    payload = _result_payload(result)
    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        raise OpenArtToolError(f"openart_model_form_get result has no 'defaults' object: {payload!r}")
    return defaults


def _build_image_params(
    form_defaults: dict[str, object],
    prompt: str,
    *,
    visual_references: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Map `prompt`, Reels-specific overrides, and (for image2image) a
    reference-image list onto a model's real form defaults. Confirmed live:
    `prompt`-shaped and `visualReferences`-shaped fields are typically
    *absent* from `defaults` itself -- required fields with no usable
    default (an empty string / empty list) get left out entirely rather
    than including a useless placeholder. Both are checked against
    `defaults`' own keys first in case a model differs; otherwise the
    primary candidate name is added directly."""
    params = dict(form_defaults)

    prompt_field = next(
        (candidate for candidate in _PROMPT_FIELD_CANDIDATES if candidate in params),
        _PROMPT_FIELD_CANDIDATES[0],
    )
    params[prompt_field] = prompt

    if "aspectRatio" in params:
        params["aspectRatio"] = _TARGET_ASPECT_RATIO

    if visual_references is not None:
        reference_field = next(
            (candidate for candidate in _REFERENCE_FIELD_CANDIDATES if candidate in params),
            _REFERENCE_FIELD_CANDIDATES[0],
        )
        params[reference_field] = visual_references

    return params


async def _upload_reference_image(
    session: ClientSession, image_bytes: bytes, *, filename: str, content_type: str
) -> dict[str, object]:
    """Upload a reference image for image2image generation. Confirmed live
    2-step flow: `openart_upload_sign` returns a GCS `signURL` to PUT the
    raw bytes to directly (not through the MCP tool layer) plus the
    `visualReference` object to use in `params.visualReferences` later;
    the upload is processed asynchronously, so `openart_upload_list` is
    polled briefly until it appears (confirmed live: ready within ~2s for
    a ~660KB JPEG)."""
    sign_result = await session.call_tool(
        "openart_upload_sign",
        arguments={
            "mediaType": "image",
            "size": len(image_bytes),
            "contentType": content_type,
            "filename": filename,
            "purpose": "create-image",
        },
    )
    sign_data = _result_payload(sign_result)
    upload_id = sign_data.get("uploadId")
    sign_url = sign_data.get("signURL")
    visual_reference = sign_data.get("visualReference")
    if not isinstance(upload_id, str) or not isinstance(sign_url, str) or not isinstance(
        visual_reference, dict
    ):
        raise OpenArtToolError(f"openart_upload_sign result missing expected fields: {sign_data!r}")

    async with httpx.AsyncClient(timeout=60.0) as client:
        put_response = await client.put(
            sign_url,
            content=image_bytes,
            headers={"Content-Type": content_type, "Content-Length": str(len(image_bytes))},
        )
        put_response.raise_for_status()

    for _attempt in range(_UPLOAD_READY_POLL_ATTEMPTS):
        await anyio.sleep(_UPLOAD_READY_POLL_SECONDS)
        list_result = await session.call_tool(
            "openart_upload_list", arguments={"mediaType": "image", "limit": 5}
        )
        list_data = _result_payload(list_result)
        items = list_data.get("items")
        if isinstance(items, list) and any(
            isinstance(item, dict) and item.get("id") == upload_id for item in items
        ):
            return visual_reference

    raise OpenArtToolError(f"reference image upload {upload_id!r} did not become ready in time")


def _extract_history_id(result: CallToolResult) -> str | None:
    payload = _result_payload(result)
    history_id = payload.get("historyId")
    return history_id if isinstance(history_id, str) and history_id else None


def _extract_asset_url(result: CallToolResult) -> str | None:
    """Pull an image (or video) URL out of a tool result if one is present.
    Returns `None` (rather than raising) when nothing url-shaped is found
    yet -- this doubles as the polling loop's "still processing" check.
    A genuine tool error (`result.isError`) still raises immediately.

    Confirmed live: a completed `openart_creation_wait`/`_get` result
    doesn't put the URL at a flat top-level key at all -- it nests inside a
    `resources` array (`{"status":"COMPLETED",...,"resources":[{"url":
    "https://...","mediaType":"image",...}]}`). The flat-key checks stay as
    a defensive first pass (cheap, and covers any tool that does return one
    directly); `resources` is the shape actually observed."""
    payload = _result_payload(result)
    for key in ("image_url", "video_url", "url", "output_url", "asset_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value

    resources = payload.get("resources")
    if isinstance(resources, list):
        for resource in resources:
            if isinstance(resource, dict):
                url = resource.get("url")
                if isinstance(url, str) and url.startswith("http"):
                    return url

    if not result.isError:
        for block in result.content:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text.strip().startswith("http"):
                return text.strip()

    return None


def _extract_poll_after_seconds(result: CallToolResult) -> float:
    """OpenArt's own `pollAfterSeconds` hint on a still-processing result --
    confirmed live: `openart_creation_wait` does NOT itself block for the
    full `timeoutSeconds` when there's no update yet; it returns almost
    immediately with this hint for how long the *caller* should wait before
    polling again. A tight poll loop that ignores this burns through its
    entire attempt budget in seconds, nowhere near enough real wall-clock
    time for generation to finish -- exactly what happened on the first
    live run of this path."""
    payload = _result_payload(result)
    value = payload.get("pollAfterSeconds")
    return float(value) if isinstance(value, (int, float)) else _DEFAULT_POLL_AFTER_SECONDS


async def _wait_for_asset_url(session: ClientSession, history_id: str) -> str:
    for attempt in range(_MAX_POLL_ATTEMPTS):
        wait_result = await session.call_tool(
            "openart_creation_wait",
            arguments={"historyId": history_id, "timeoutSeconds": _POLL_TIMEOUT_SECONDS},
        )
        asset_url = _extract_asset_url(wait_result)
        if asset_url is not None:
            return asset_url
        poll_after = _extract_poll_after_seconds(wait_result)
        logger.info(
            "openart_asset_still_processing",
            history_id=history_id,
            attempt=attempt,
            poll_after_seconds=poll_after,
        )
        await anyio.sleep(poll_after)

    raise OpenArtToolError(f"OpenArt generation timed out waiting for historyId={history_id!r}")


def _unwrap_single(exc: BaseException) -> BaseException:
    """anyio's TaskGroup (used internally by `streamablehttp_client`) wraps
    any exception raised inside its `async with` block in a
    `BaseExceptionGroup`, discarding the original message behind a generic
    "unhandled errors in a TaskGroup" string -- confirmed live: this hid our
    own clear `OpenArtToolError` messages (timeouts, tool errors) behind
    that generic wrapper, making failures nearly undebuggable from the
    orchestrator's log line alone. Recursively unwraps single-exception
    groups down to the real underlying exception."""
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


async def _generate_image_impl(
    prompt: str, *, reference_image: bytes | None, reference_filename: str, reference_content_type: str
) -> bytes:
    mode = _IMAGE_TO_IMAGE_MODE if reference_image is not None else _TEXT_TO_IMAGE_MODE

    provider = build_openart_oauth_provider()
    async with (
        streamablehttp_client(OPENART_MCP_URL, auth=provider) as (read, write, _get_session_id),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = (await session.list_tools()).tools
        _ensure_tools_available(tools)

        visual_references = None
        if reference_image is not None:
            visual_reference = await _upload_reference_image(
                session,
                reference_image,
                filename=reference_filename,
                content_type=reference_content_type,
            )
            visual_references = [visual_reference]

        form_result = await session.call_tool(
            "openart_model_form_get",
            arguments={"model": _DEFAULT_MODEL, "mode": mode},
        )
        form_defaults = _extract_form_defaults(form_result)
        params = _build_image_params(form_defaults, prompt, visual_references=visual_references)

        logger.info("openart_image_generation_started", model=_DEFAULT_MODEL, mode=mode)
        gen_result = await session.call_tool(
            "openart_generate_image",
            arguments={"model": _DEFAULT_MODEL, "mode": mode, "params": params},
        )

        asset_url = _extract_asset_url(gen_result)
        if asset_url is None:
            history_id = _extract_history_id(gen_result)
            if history_id is None:
                raise OpenArtToolError(
                    f"openart_generate_image result has neither an image URL nor a "
                    f"'historyId' to poll: {gen_result!r}"
                )
            asset_url = await _wait_for_asset_url(session, history_id)

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.get(asset_url)
        response.raise_for_status()
        return response.content


async def generate_image(
    prompt: str,
    *,
    reference_image: bytes | None = None,
    reference_filename: str = "reference.jpg",
    reference_content_type: str = "image/jpeg",
) -> bytes:
    """Full round trip: connect (OAuth-authenticated), optionally upload
    `reference_image` and switch to image2image mode (keeps the generated
    subject visually consistent with the brand's real mascot instead of
    OpenArt inventing a generic one -- see module docstring), fetch the
    target model's real form defaults, kick off generation, resolve the
    resulting image URL (directly if the call is synchronous, or by
    polling if it returns a `historyId` instead), download the image.
    Raises on any failure -- see module docstring. Unwraps anyio's
    `BaseExceptionGroup` wrapping (see `_unwrap_single`) so callers see the
    real error."""
    try:
        return await _generate_image_impl(
            prompt,
            reference_image=reference_image,
            reference_filename=reference_filename,
            reference_content_type=reference_content_type,
        )
    except BaseExceptionGroup as eg:
        raise _unwrap_single(eg) from eg
