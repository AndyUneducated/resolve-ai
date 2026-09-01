"""Salesforce Mock MCP Server (real impl using the official `mcp` Python SDK).

Tools:
- get_account(customer_id) → Account                       (capability=read)
- update_opportunity(opportunity_id, stage?, amount?) → Opp (capability=write)

`update_opportunity` must appear in the agent's whitelist (decision 4 · Layer 2).
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
        "name": "get_account",
        "description": "Fetch a Salesforce account by customer_id.",
        "capability": "read",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "update_opportunity",
        "description": (
            "Update an opportunity's stage and/or amount. Write action; the "
            "calling agent must have 'salesforce.update_opportunity' granted."
        ),
        "capability": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "opportunity_id": {"type": "string"},
                "stage": {
                    "type": "string",
                    "enum": [
                        "prospecting",
                        "negotiation",
                        "closed_won",
                        "closed_lost",
                    ],
                },
                "amount": {"type": "number"},
            },
            "required": ["opportunity_id"],
        },
    },
]

server: Server = Server("salesforce")


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
    if name == "get_account":
        return _ok(data.get_account(**args))
    if name == "update_opportunity":
        return _ok(data.update_opportunity(**args))
    raise ValueError(f"unknown_tool: {name}")


async def _serve() -> None:
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main() -> None:
    asyncio.run(_serve())
