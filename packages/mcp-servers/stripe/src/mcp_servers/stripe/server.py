"""Stripe Mock MCP Server (real impl using the official `mcp` Python SDK).

Tools:
- list_charges(customer_id, limit?) → list[Charge]            (capability=read)
- get_charge(charge_id) → Charge                              (capability=read)
- refund(charge_id, amount?) → Refund                         (capability=destructive)

`refund` can be called only when it is explicitly included in the agent's
capability whitelist (Decision 4 · Layer 2).
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
        "name": "list_charges",
        "description": "List recent charges for a Stripe customer.",
        "capability": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "get_charge",
        "description": "Fetch a single charge by ID.",
        "capability": "read",
        "input_schema": {
            "type": "object",
            "properties": {"charge_id": {"type": "string"}},
            "required": ["charge_id"],
        },
    },
    {
        "name": "refund",
        "description": (
            "Refund a charge in full or partial. Destructive action; the calling "
            "agent must have 'stripe.refund' in its capability whitelist."
        ),
        "capability": "destructive",
        "input_schema": {
            "type": "object",
            "properties": {
                "charge_id": {"type": "string"},
                "amount": {
                    "type": "integer",
                    "description": "Minor units; omit for full refund.",
                },
            },
            "required": ["charge_id"],
        },
    },
]

server: Server = Server("stripe")


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
    if name == "list_charges":
        return _ok(data.list_charges(**args))
    if name == "get_charge":
        return _ok(data.get_charge(**args))
    if name == "refund":
        return _ok(data.refund(**args))
    raise ValueError(f"unknown_tool: {name}")


async def _serve() -> None:
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main() -> None:
    asyncio.run(_serve())
