"""Fake MCP transport for `lib.crew.reels.openart_client` round-trip tests.

Replaces everything below the tool-call boundary -- the OAuth provider, the
streamable-HTTP connection, the `ClientSession`, and both `httpx` clients
(the reference PUT and the final image download) -- with in-memory doubles
that answer in OpenArt's confirmed-live result shapes and record every call.

Nothing here asserts anything about OpenArt's real network behavior; the
point is to make the CALL SHAPE `openart_client` builds observable, which
is otherwise reachable only in production.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from lib.crew.reels import openart_client, openart_session

TOOL_NAMES = (
    "openart_model_form_get",
    "openart_generate_image",
    "openart_creation_wait",
    "openart_upload_sign",
    "openart_upload_list",
)
IMAGE_BYTES = b"generated-image-bytes"
DEFAULT_FORM_DEFAULTS: dict[str, object] = {"prompt": "", "aspectRatio": "1:1", "seed": -1}


def tool_result(payload: dict[str, object]) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))])


class FakeSession:
    """Records every tool call and answers with confirmed-live shapes."""

    def __init__(self, defaults: dict[str, object]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.puts: list[str] = []
        self._defaults = defaults
        self._uploaded: list[str] = []

    async def initialize(self) -> None:
        return None

    async def list_tools(self) -> SimpleNamespace:
        return SimpleNamespace(
            tools=[
                Tool(
                    name=name,
                    description="",
                    inputSchema={"type": "object", "properties": {f"marker_{name}": {}}},
                )
                for name in TOOL_NAMES
            ]
        )

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        args = arguments or {}
        self.calls.append((name, args))
        if name == "openart_upload_sign":
            upload_id = f"up{len(self._uploaded) + 1}"
            self._uploaded.append(upload_id)
            return tool_result(
                {
                    "uploadId": upload_id,
                    "signURL": f"https://upload.example/{upload_id}",
                    "visualReference": {
                        "type": "image",
                        "id": f"vr-{upload_id}",
                        "url": f"https://cdn.example/{upload_id}.jpg",
                        "label": args.get("filename", ""),
                    },
                }
            )
        if name == "openart_upload_list":
            return tool_result({"items": [{"id": uid} for uid in self._uploaded]})
        if name == "openart_model_form_get":
            return tool_result({"model": "nano-banana-2-lite", "defaults": self._defaults})
        if name == "openart_generate_image":
            return tool_result({"image_url": "https://cdn.example/out.png"})
        raise AssertionError(f"unexpected tool call: {name}")  # noqa: S101

    def tool_calls(self, name: str) -> list[dict[str, Any]]:
        return [args for called, args in self.calls if called == name]

    @property
    def generate_params(self) -> dict[str, Any]:
        return self.tool_calls("openart_generate_image")[0]["params"]

    @property
    def generate_mode(self) -> str:
        return self.tool_calls("openart_generate_image")[0]["mode"]


class FakeResponse:
    def __init__(self, content: bytes = b"") -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


def fake_httpx(puts: list[str]) -> SimpleNamespace:
    """Stand-in for the `httpx` module: PUTs are recorded, GETs return the
    same fixed image bytes the generate path is expected to hand back."""

    class _Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_exc: object) -> bool:
            return False

        async def get(self, _url: str) -> FakeResponse:
            return FakeResponse(IMAGE_BYTES)

        async def put(self, url: str, **_kwargs: object) -> FakeResponse:
            puts.append(url)
            return FakeResponse()

    return SimpleNamespace(AsyncClient=_Client)


def install(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    """Patch the transport out of both OpenArt modules and hand back the
    session that recorded the run. The upload readiness sleep is zeroed so
    the cache tests don't pay 2s per upload."""
    session = FakeSession(dict(DEFAULT_FORM_DEFAULTS))

    class _Connection:
        async def __aenter__(self) -> tuple[str, str, object]:
            return ("read", "write", lambda: "session-id")

        async def __aexit__(self, *_exc: object) -> bool:
            return False

    class _SessionCtx:
        def __init__(self, *_args: object) -> None:
            return None

        async def __aenter__(self) -> FakeSession:
            return session

        async def __aexit__(self, *_exc: object) -> bool:
            return False

    monkeypatch.setattr(openart_client, "build_openart_oauth_provider", lambda: None)
    monkeypatch.setattr(openart_client, "streamablehttp_client", lambda *a, **k: _Connection())
    monkeypatch.setattr(openart_client, "ClientSession", _SessionCtx)
    monkeypatch.setattr(openart_client, "httpx", fake_httpx(session.puts))
    monkeypatch.setattr(openart_session, "httpx", fake_httpx(session.puts))
    monkeypatch.setattr(openart_session, "_UPLOAD_READY_POLL_SECONDS", 0.0)
    return session
