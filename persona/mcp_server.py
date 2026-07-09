"""Persona MCP Server.

Wraps the Persona FastAPI approval/schedule API as an MCP server so Claude
(or any MCP-compatible client) can manage the social automation pipeline
directly in conversation.

Exposed tools:
  list_pending        — List items awaiting approval (posts, groups, campaigns)
  approve_item        — Approve a pending item (optionally with edited text)
  reject_item         — Reject/skip a pending item
  get_item            — Get details of a single item by ID
  list_workers        — List all scheduled workers with last-run status
  trigger_worker      — Manually trigger a scheduled worker
  get_worker_log      — Get recent log output from a worker
  get_activity        — Get recent engagement activity log
  list_fb_groups      — List tracked Facebook groups

Usage (stdio transport — for Claude Code / local clients):
    python mcp_server.py

Usage (SSE transport — for remote clients / web UI):
    python mcp_server.py --transport sse --port 8765

Environment variables:
    PERSONA_API_URL  — Base URL of the Persona FastAPI server
                       (default: http://localhost:5001)
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE = os.environ.get("PERSONA_API_URL", "http://localhost:5001")
_client = httpx.Client(base_url=API_BASE, timeout=30.0)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _get(path: str, **params: Any) -> Any:
    r = _client.get(f"/api/v1{path}", params=params)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict[str, Any] | None = None) -> Any:
    r = _client.post(f"/api/v1{path}", json=body or {})
    r.raise_for_status()
    return r.json()


def _fmt(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS: list[Tool] = [
    Tool(
        name="list_pending",
        description=(
            "List all items currently waiting for approval: social posts, "
            "blog-post pairs, group-join candidates, campaign verifications."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "type_filter": {
                    "type": "string",
                    "description": "Optional filter: 'blog_post', 'group', 'campaign', 'comment'",
                }
            },
        },
    ),
    Tool(
        name="approve_item",
        description="Approve a pending item. For blog posts, optionally supply edited text.",
        inputSchema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "Item ID from list_pending"},
                "edited_text": {
                    "type": "string",
                    "description": "Optional: edited caption/body to use instead of the generated text",
                },
            },
            "required": ["item_id"],
        },
    ),
    Tool(
        name="reject_item",
        description="Reject / skip a pending item.",
        inputSchema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "Item ID from list_pending"},
                "reason": {"type": "string", "description": "Optional rejection reason for the log"},
            },
            "required": ["item_id"],
        },
    ),
    Tool(
        name="get_item",
        description="Get full details of a single pending item by ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
            },
            "required": ["item_id"],
        },
    ),
    Tool(
        name="list_workers",
        description=(
            "List all scheduled workers (fb-scanner, ig-scanner, comment-composer, etc.) "
            "with their last-run time and status."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="trigger_worker",
        description="Manually trigger a scheduled worker by label (e.g. 'fb-scanner').",
        inputSchema={
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Worker label from list_workers"},
            },
            "required": ["label"],
        },
    ),
    Tool(
        name="get_worker_log",
        description="Get recent log output from a specific worker.",
        inputSchema={
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Worker label"},
                "lines": {"type": "integer", "description": "Number of lines to return (default: 50)"},
            },
            "required": ["label"],
        },
    ),
    Tool(
        name="get_activity",
        description="Get recent engagement activity (likes, comments, group posts, follows).",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max entries to return (default: 20)"},
                "platform": {
                    "type": "string",
                    "description": "Optional filter: 'facebook', 'instagram', 'wordpress'",
                },
            },
        },
    ),
    Tool(
        name="list_fb_groups",
        description=(
            "List all tracked Facebook groups with their status, posting mode, "
            "member count, and last post info."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "description": "Optional filter: 'active', 'pending_approval', 'blocked'",
                }
            },
        },
    ),
]

# ── Request handler ───────────────────────────────────────────────────────────


async def handle_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        result = _dispatch(name, arguments)
        return [TextContent(type="text", text=_fmt(result))]
    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"API error {e.response.status_code}: {e.response.text}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


def _dispatch(name: str, args: dict[str, Any]) -> Any:
    if name == "list_pending":
        data = _get("/pending")
        items = data.get("items", [])
        if f := args.get("type_filter"):
            items = [i for i in items if i.get("type") == f]
        return {"count": len(items), "items": items}

    elif name == "approve_item":
        item_id = args["item_id"]
        body: dict[str, Any] = {}
        if edited := args.get("edited_text"):
            # Try edit first, then approve
            try:
                _post(f"/items/{item_id}/edit", {"text": edited})
            except Exception:
                pass
        return _post(f"/items/{item_id}/approve", body)

    elif name == "reject_item":
        item_id = args["item_id"]
        body = {"reason": args.get("reason", "")}
        return _post(f"/items/{item_id}/reject", body)

    elif name == "get_item":
        return _get(f"/items/{args['item_id']}")

    elif name == "list_workers":
        return _get("/workers")

    elif name == "trigger_worker":
        return _post(f"/workers/{args['label']}/trigger")

    elif name == "get_worker_log":
        lines = args.get("lines", 50)
        return _get(f"/workers/{args['label']}/log", lines=lines)

    elif name == "get_activity":
        params: dict[str, Any] = {"limit": args.get("limit", 20)}
        if p := args.get("platform"):
            params["platform"] = p
        return _get("/activity", **params)

    elif name == "list_fb_groups":
        data = _get("/facebook/groups")
        groups = data.get("groups", [])
        if sf := args.get("status_filter"):
            groups = [g for g in groups if g.get("status") == sf]
        return {"count": len(groups), "groups": groups}

    else:
        raise ValueError(f"Unknown tool: {name}")


# ── Server entry point ────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Persona MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8765, help="Port for SSE transport")
    args = parser.parse_args()

    server = Server("persona")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        return await handle_tool(name, arguments)

    if args.transport == "stdio":
        import asyncio
        asyncio.run(stdio_server(server))
    else:
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Route
        import uvicorn

        sse = SseServerTransport("/messages")

        async def handle_sse(request: Any) -> Any:
            async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
                await server.run(streams[0], streams[1], server.create_initialization_options())

        starlette_app = Starlette(routes=[Route("/sse", endpoint=handle_sse)])
        uvicorn.run(starlette_app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
