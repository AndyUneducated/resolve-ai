"""Slack Mock MCP Server (real impl using the official `mcp` Python SDK).

Tools:
- notify_team(channel, message, mention?) → Message       (capability=write)
- post_message(channel, message)          → Message       (capability=write)

Both tools require the agent's whitelist to include the full name
(`slack.notify_team` / `slack.post_message`) — see decision 4 · Layer 2.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import data

TOOLS: list[dict[str, Any]] = [
    {
        "name": "notify_team",
        "description": (
            "Notify an on-call team channel with an optional @-mention. "
            "Used by Escalation Agent for human handoff."
        ),
        "capability": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "message": {"type": "string"},
                "mention": {"type": "string"},
            },
            "required": ["channel", "message"],
        },
    },
    {
        "name": "post_message",
        "description": "Post a generic message to a channel (no @-mention).",
        "capability": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["channel", "message"],
        },
    },
]

server: Server = Server("slack")


@server.list_tools()
async def _list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t["input_schema"],
        )
        for t in TOOLS
    ]


def _ok(payload: object) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(payload, default=str))]


@server.call_tool()
async def _call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[types.TextContent]:
    args = arguments or {}
    if name == "notify_team":
        return _ok(data.notify_team(**args))
    if name == "post_message":
        return _ok(data.post_message(**args))
    raise ValueError(f"unknown_tool: {name}")


async def _serve() -> None:
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main() -> None:
    asyncio.run(_serve())
