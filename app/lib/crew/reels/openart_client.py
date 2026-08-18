"""OpenArt MCP client: turn one `ReelBeat.image_prompt` string into a
downloaded image, one call per beat (5 total for a full reel).

Auth is `lib.oauth.openart.build_openart_oauth_provider()` -- it drives the
one-time interactive consent flow (if no token is stored yet) or
transparently refreshes a stored token, so this module never handles
credentials itself. Tool checks, reference uploads and their per-run cache
live in `lib.crew.reels.openart_session`.

**Real OpenArt tool contract (confirmed live).** `openart_model_form_get(model,
mode)` returns a flat `defaults` dict for that model+mode's dynamic
parameter schema -- the one part of the response that's consistent across
models regardless of schema complexity; `_build_image_params` maps our
prompt text and Reels-specific overrides (vertical aspect ratio, reference
images, seed) onto it, rather than assuming a fixed shape. `_DEFAULT_MODEL`
is a single hardcoded choice (`nano-banana-2-lite`) -- OpenArt's own catalog
describes it as "fastest, most cost-efficient... pick when speed or cost
matters more than maximum quality," matching why this path replaced
OpenArt's (far more expensive) video generation in the first place.

**Two modes, chosen per call.** `text2image` (no references) has nothing to
ground the subject's actual appearance. `image2image` additionally takes a
`visualReferences` array (confirmed live shape: `{type:"image", id, url,
label}`, exactly what `openart_upload_sign` returns) -- passing the brand's
own mascot photos there keeps the generated dog visually consistent with
the real mascot instead of OpenArt inventing a generic one. That field has
always been a *list*: `generate_image` takes a `references` sequence and
switches mode automatically when it is non-empty.

`openart_generate_image` may answer synchronously or with a `historyId` to
poll (confirmed live, same as `openart_generate_video`); both are handled.

Raises on any failure -- auth expiry, unknown tool, tool error, insufficient
credits, timeout, network error -- all propagate identically, deliberately
NOT distinguishing credit exhaustion: the orchestrator catches broadly and
falls back to the deterministic (reused hero image) path regardless.
"""

from __future__ import annotations

from collections.abc import Sequence

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult

from lib.crew.reels.openart_session import (
    OpenArtToolError,
    ReferenceCache,
    ReferenceUpload,
    _ensure_tools_available,
    _extract_form_defaults,
    _result_payload,
    _unwrap_single,
    log_model_capabilities,
    resolve_visual_references,
)
from lib.oauth.openart import OPENART_MCP_URL, build_openart_oauth_provider, extract_auth_required
from lib.observability import get_logger

logger = get_logger(__name__)

__all__ = ["OpenArtToolError", "ReferenceCache", "ReferenceUpload", "generate_image"]

_PROMPT_FIELD_CANDIDATES = ("prompt", "instructions", "text", "description")
_REFERENCE_FIELD_CANDIDATES = ("visualReferences", "referenceImages", "images")

_DEFAULT_MODEL = "nano-banana-2-lite"
_TEXT_TO_IMAGE_MODE = "text2image"
_IMAGE_TO_IMAGE_MODE = "image2image"
_TARGET_ASPECT_RATIO = "9:16"

# The MCP SDK's own HTTP timeout defaults to 30s, SHORTER than an
# `openart_generate_image` submit legitimately takes -- confirmed live, a beat
# died with `{"status":"FAILED","historyId":"submit-failed","error":"The
# operation was aborted due to timeout"}` while its siblings succeeded: the
# request was aborted client-side mid-submit, not a real generation failure.
_MCP_HTTP_TIMEOUT = 180.0
_POLL_TIMEOUT_SECONDS = 90
_MAX_POLL_ATTEMPTS = 60  # confirmed live: a real generation outlived a 12-attempt (~70s) budget
_DEFAULT_POLL_AFTER_SECONDS = 5.0


def _build_image_params(
    form_defaults: dict[str, object],
    prompt: str,
    *,
    visual_references: list[dict[str, object]] | None = None,
    seed: int | None = None,
) -> dict[str, object]:
    """Map `prompt`, Reels-specific overrides, and (for image2image) a
    reference-image list onto a model's real form defaults. Confirmed live:
    `prompt`- and `visualReferences`-shaped fields are typically *absent*
    from `defaults` itself (a required field whose only default would be an
    empty string / empty list is left out entirely), so each is looked up
    among `defaults`' own keys first and otherwise added under its primary
    candidate name.

    `seed` is the exception -- applied ONLY when `defaults` already declares
    a `seed` key, so it can never inject a field the model would reject."""
    params = dict(form_defaults)

    prompt_field = next(
        (candidate for candidate in _PROMPT_FIELD_CANDIDATES if candidate in params),
        _PROMPT_FIELD_CANDIDATES[0],
    )
    params[prompt_field] = prompt

    if "aspectRatio" in params:
        params["aspectRatio"] = _TARGET_ASPECT_RATIO

    if seed is not None and "seed" in params:
        params["seed"] = seed

    if visual_references is not None:
        reference_field = next(
            (candidate for candidate in _REFERENCE_FIELD_CANDIDATES if candidate in params),
            _REFERENCE_FIELD_CANDIDATES[0],
        )
        params[reference_field] = visual_references

    return params


def _extract_history_id(result: CallToolResult) -> str | None:
    payload = _result_payload(result)
    history_id = payload.get("historyId")
    return history_id if isinstance(history_id, str) and history_id else None


def _extract_asset_url(result: CallToolResult) -> str | None:
    """Pull an image (or video) URL out of a tool result if one is present.
    Returns `None` (rather than raising) when nothing url-shaped is found
    yet -- this doubles as the polling loop's "still processing" check.
    A genuine tool error (`result.isError`) still raises immediately.

    Confirmed live: a completed `openart_creation_wait`/`_get` result nests
    the URL inside a `resources` array (`{"status":"COMPLETED",...,
    "resources":[{"url":"https://...","mediaType":"image",...}]}`), never at
    a flat top-level key. The flat-key checks stay as a cheap defensive
    first pass; `resources` is the shape actually observed."""
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
    """OpenArt's own `pollAfterSeconds` hint on a still-processing result.
    Confirmed live: `openart_creation_wait` does NOT block for the full
    `timeoutSeconds` when there's no update yet -- it returns almost
    immediately with this hint, and a loop that ignores it burns its whole
    attempt budget in seconds (exactly what this path's first live run
    did)."""
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


async def _generate_image_impl(
    prompt: str,
    *,
    references: Sequence[ReferenceUpload],
    seed: int | None,
    reference_cache: ReferenceCache | None,
) -> bytes:
    mode = _IMAGE_TO_IMAGE_MODE if references else _TEXT_TO_IMAGE_MODE

    provider = build_openart_oauth_provider()
    async with (
        streamablehttp_client(OPENART_MCP_URL, auth=provider, timeout=_MCP_HTTP_TIMEOUT) as (
            read,
            write,
            _get_session_id,
        ),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = (await session.list_tools()).tools
        _ensure_tools_available(tools)

        visual_references = await resolve_visual_references(session, references, reference_cache)

        form_result = await session.call_tool(
            "openart_model_form_get",
            arguments={"model": _DEFAULT_MODEL, "mode": mode},
        )
        log_model_capabilities(tools, form_result, model=_DEFAULT_MODEL, mode=mode)
        form_defaults = _extract_form_defaults(form_result)
        params = _build_image_params(
            form_defaults, prompt, visual_references=visual_references or None, seed=seed
        )

        logger.info(
            "openart_image_generation_started",
            model=_DEFAULT_MODEL,
            mode=mode,
            references=len(visual_references),
        )
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
    references: Sequence[ReferenceUpload] = (),
    seed: int | None = None,
    reference_cache: ReferenceCache | None = None,
    reference_image: bytes | None = None,
    reference_filename: str = "reference.jpg",
    reference_content_type: str = "image/jpeg",
) -> bytes:
    """Full round trip: connect (OAuth-authenticated), upload any
    `references` (cache-permitting) and switch to image2image mode, fetch
    the model's real form defaults, kick off generation, resolve the image
    URL (directly, or by polling a `historyId`), download the image.

    `reference_cache` should be one `ReferenceCache` shared by every call in
    a pipeline run, so a photo used by all five beats is uploaded once.
    `seed` is honored only when the model's own defaults declare a `seed`
    field (see `_build_image_params`). `reference_image` /
    `reference_filename` / `reference_content_type` are the deprecated
    single-reference form, folded into `references` as a trailing entry.
    Raises on any failure -- see module docstring."""
    all_references = list(references)
    if reference_image is not None:
        all_references.append(
            ReferenceUpload(reference_image, reference_filename, reference_content_type)
        )
    try:
        return await _generate_image_impl(
            prompt, references=all_references, seed=seed, reference_cache=reference_cache
        )
    except BaseExceptionGroup as eg:
        # Auth-required must survive even multi-exception groups (teardown
        # errors ride along) so callers can catch the TYPED error, not a group.
        auth_error = extract_auth_required(eg)
        if auth_error is not None:
            raise auth_error from eg
        raise _unwrap_single(eg) from eg
