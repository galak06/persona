"""Everything `openart_client`'s generate round trip leans on: tool
availability checks, tool-result payload parsing, unwrapping the
`BaseExceptionGroup` this session's own TaskGroup throws around real
errors, reference-image upload plus its per-run cache, and capability
logging. Split out of `openart_client.py` so both files stay under the
project's 300-line cap.

**Uploading a reference is a confirmed-live 2-step flow.**
`openart_upload_sign` returns a `signURL` (a GCS resumable-upload URL) and
the `visualReference` object to use later; the raw bytes get PUT to
`signURL` directly (not through the MCP tool layer), then
`openart_upload_list` is polled briefly until the upload shows up
(confirmed live: ready within ~2s for a ~660KB JPEG).

**Why a cache, not a longer session.** That flow sleeps 2s *before* its
first readiness poll, on top of two tool round trips. A reel generates one
image per beat (5 calls) and each call builds its own MCP session, so
multi-reference support would otherwise turn one upload into 5+ per photo.
`ReferenceCache` is owned by the pipeline run and passed into every
`generate_image` call, making uploads scale with *distinct photos* rather
than with beats -- safe across sessions because the `visualReference`
OpenArt hands back is a plain `{type, id, url, label}` record, not a
session-scoped handle.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass

import anyio
import httpx
from mcp import ClientSession
from mcp.types import CallToolResult, Tool

from lib.observability import get_logger

logger = get_logger(__name__)

_REQUIRED_TOOL_NAMES = (
    "openart_model_form_get",
    "openart_generate_image",
    "openart_creation_wait",
    "openart_upload_sign",
    "openart_upload_list",
)
_GENERATE_IMAGE_TOOL = "openart_generate_image"

_UPLOAD_READY_POLL_ATTEMPTS = 6
_UPLOAD_READY_POLL_SECONDS = 2.0
# Was 5. With several references uploaded back to back, a just-signed id can
# fall outside a 5-item "most recent uploads" window before it is polled for.
_UPLOAD_LIST_LIMIT = 25

_SCHEMA_LOG_MAX_CHARS = 8000


class OpenArtToolError(RuntimeError):
    """Raised when OpenArt's MCP server is missing an expected tool, a call
    to it errors, or its result doesn't yield the data the next step needs."""


@dataclass(frozen=True)
class ReferenceUpload:
    """One reference image to ground an image2image generation: the raw
    bytes, plus what `openart_upload_sign` is told about them."""

    data: bytes
    filename: str = "reference.jpg"
    content_type: str = "image/jpeg"


class ReferenceCache:
    """Per-run memo of `visualReference` records, keyed on `sha256(data)[:16]`
    so identical bytes hit regardless of which `ReferenceUpload` carried
    them (see the module docstring for why it's owned by the run)."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, object]] = {}

    @staticmethod
    def key(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()[:16]

    def get(self, data: bytes) -> dict[str, object] | None:
        return self._records.get(self.key(data))

    def put(self, data: bytes, record: dict[str, object]) -> None:
        self._records[self.key(data)] = record

    def __len__(self) -> int:
        return len(self._records)


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
        raise OpenArtToolError(
            f"openart_model_form_get result has no 'defaults' object: {payload!r}"
        )
    return defaults


def _unwrap_single(exc: BaseException) -> BaseException:
    """anyio's TaskGroup (used internally by `streamablehttp_client`) wraps
    anything raised inside its `async with` block in a `BaseExceptionGroup`,
    hiding the real message behind a generic "unhandled errors in a
    TaskGroup" string -- confirmed live, this made our own clear
    `OpenArtToolError` messages undebuggable from the orchestrator's log
    line. Recursively unwraps single-exception groups."""
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


async def _upload_reference_image(
    session: ClientSession, image_bytes: bytes, *, filename: str, content_type: str
) -> dict[str, object]:
    """Upload one reference image and return its `visualReference` record
    (module docstring has the confirmed-live 2-step flow). Callers should go
    through `resolve_visual_references` -- this always pays the full upload
    cost, readiness sleep included."""
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
    if (
        not isinstance(upload_id, str)
        or not isinstance(sign_url, str)
        or not isinstance(visual_reference, dict)
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
            "openart_upload_list",
            arguments={"mediaType": "image", "limit": _UPLOAD_LIST_LIMIT},
        )
        list_data = _result_payload(list_result)
        items = list_data.get("items")
        if isinstance(items, list) and any(
            isinstance(item, dict) and item.get("id") == upload_id for item in items
        ):
            return visual_reference

    raise OpenArtToolError(f"reference image upload {upload_id!r} did not become ready in time")


async def resolve_visual_references(
    session: ClientSession,
    references: Sequence[ReferenceUpload],
    cache: ReferenceCache | None,
) -> list[dict[str, object]]:
    """`visualReferences` records for `references`, uploading only the ones
    `cache` hasn't already seen (order preserved).

    The cache is what keeps this from scaling with beats: a reel's five
    generate calls share one, so each distinct photo is uploaded exactly
    once for the whole run."""
    records: list[dict[str, object]] = []
    for reference in references:
        cached = cache.get(reference.data) if cache is not None else None
        if cached is not None:
            records.append(cached)
            continue
        record = await _upload_reference_image(
            session,
            reference.data,
            filename=reference.filename,
            content_type=reference.content_type,
        )
        if cache is not None:
            cache.put(reference.data, record)
        records.append(record)
    return records


def _schema_logging_enabled() -> bool:
    return os.environ.get("OPENART_LOG_SCHEMA", "") == "1" or logging.getLogger().isEnabledFor(
        logging.DEBUG
    )


def _truncated_json(value: object) -> str:
    text = json.dumps(value, default=str)
    if len(text) <= _SCHEMA_LOG_MAX_CHARS:
        return text
    return text[:_SCHEMA_LOG_MAX_CHARS] + "...[truncated]"


def log_model_capabilities(
    tools: list[Tool], form_result: CallToolResult, *, model: str, mode: str
) -> None:
    """Dump what OpenArt declares it accepts -- discovery only, no behavior.

    Two payloads the generate path already holds and throws away:

      * `openart_tool_input_schema` -- `openart_generate_image`'s own
        `inputSchema`. `list_tools()` runs every call, but only `.name` is
        ever inspected.
      * `openart_model_form_payload` -- the FULL `openart_model_form_get`
        response. `_extract_form_defaults` keeps `payload["defaults"]` and
        discards the rest, which is exactly where a `strength` /
        `fidelity` / `guidance_scale` field or a model catalog would show up.

    Emitted at DEBUG, and only when `OPENART_LOG_SCHEMA=1` or the root
    logger is already at DEBUG, so a normal run pays nothing. Each payload
    is truncated to 8000 chars."""
    if not _schema_logging_enabled():
        return

    schema = next((tool.inputSchema for tool in tools if tool.name == _GENERATE_IMAGE_TOOL), None)
    logger.debug(
        "openart_tool_input_schema",
        tool=_GENERATE_IMAGE_TOOL,
        model=model,
        mode=mode,
        input_schema=_truncated_json(schema),
    )

    try:
        payload: object = _result_payload(form_result)
    except OpenArtToolError as exc:  # never let discovery logging break a run
        payload = {"error": str(exc)}
    logger.debug(
        "openart_model_form_payload", model=model, mode=mode, payload=_truncated_json(payload)
    )
