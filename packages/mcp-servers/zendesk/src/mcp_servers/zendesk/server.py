"""Zendesk Mock MCP Server (real impl using the official `mcp` Python SDK).

Tools:
- get_ticket_history(customer_id) → list[Ticket]            (capability=read)
- update_ticket(ticket_id, status?, note?) → Ticket         (capability=write)
- escalate(ticket_id, reason) → Ticket                      (capability=destructive)

`update_ticket` and `escalate` must appear in the agent's capability whitelist
(decision 4 · Layer 2).
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
        "name": "get_ticket_history",
        "description": "Fetch all tickets for a given customer (newest first).",
        "capability": "read",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "update_ticket",
        "description": (
            "Update ticket status and/or append an internal note. Write action; "
            "the calling agent must have 'zendesk.update_ticket' granted."
        ),
        "capability": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["open", "pending", "solved", "escalated"],
                },
                "note": {"type": "string"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "escalate",
        "description": (
            "Escalate a ticket to a human agent. Destructive action; the calling "
            "agent must have 'zendesk.escalate' granted."
        ),
        "capability": "destructive",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["ticket_id", "reason"],
        },
    },
]

server: Server = Server("zendesk")


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
    if name == "get_ticket_history":
        return _ok(data.get_ticket_history(**args))
    if name == "update_ticket":
        return _ok(data.update_ticket(**args))
    if name == "escalate":
        return _ok(data.escalate(**args))
    raise ValueError(f"unknown_tool: {name}")


async def _serve() -> None:
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main() -> None:
    asyncio.run(_serve())
