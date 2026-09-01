"""Intercom Mock MCP Server (real impl using the official `mcp` Python SDK).

Tools:
- get_conversation(conversation_id) → Conversation         (capability=read)
- tag_user(user_id, tag) → User                            (capability=write)

`tag_user` must appear in the agent's whitelist (decision 4 · Layer 2).
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
        "name": "get_conversation",
        "description": "Fetch an Intercom conversation by id.",
        "capability": "read",
        "input_schema": {
            "type": "object",
            "properties": {"conversation_id": {"type": "string"}},
            "required": ["conversation_id"],
        },
    },
    {
        "name": "tag_user",
        "description": (
            "Apply a tag to an Intercom user. Write action; the calling agent "
            "must have 'intercom.tag_user' granted."
        ),
        "capability": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tag": {"type": "string"},
            },
            "required": ["user_id", "tag"],
        },
    },
]

server: Server = Server("intercom")


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
    if name == "get_conversation":
        return _ok(data.get_conversation(**args))
    if name == "tag_user":
        return _ok(data.tag_user(**args))
    raise ValueError(f"unknown_tool: {name}")


async def _serve() -> None:
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main() -> None:
    asyncio.run(_serve())
