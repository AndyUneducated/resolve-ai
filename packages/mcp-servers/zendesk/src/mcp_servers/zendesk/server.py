"""Zendesk Mock MCP Server.

工具：
- get_ticket_history(customer_id) → list[Ticket]
- update_ticket(ticket_id, status, note)
- escalate(ticket_id, reason)

TODO: 用 mcp SDK 真正实现 stdio server；当前是占位。
"""

from __future__ import annotations

from typing import Any

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_ticket_history",
        "description": "Fetch all tickets for a given customer.",
        "capability": "read",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "update_ticket",
        "description": "Update ticket status / append internal note.",
        "capability": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "status": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "escalate",
        "description": "Escalate a ticket to a human agent.",
        "capability": "write",
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


def main() -> None:
    """TODO: 接 `mcp.server.Server` + stdio_server。"""
    import json
    import sys

    print(json.dumps({"server": "zendesk", "tools": [t["name"] for t in TOOLS]}), file=sys.stderr)
