"""Stripe Mock MCP Server.

工具：
- list_charges(customer_id) → list[Charge]
- get_charge(charge_id) → Charge
- refund(charge_id, amount?) → Refund    # write/destructive — 需 explicit grant
"""

from __future__ import annotations

from typing import Any

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_charges",
        "description": "List charges for a customer.",
        "capability": "read",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}, "limit": {"type": "integer"}},
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
        "description": "Refund a charge (full or partial). Subject to capability whitelist.",
        "capability": "destructive",
        "input_schema": {
            "type": "object",
            "properties": {
                "charge_id": {"type": "string"},
                "amount": {"type": "integer", "description": "minor units; null = full refund"},
            },
            "required": ["charge_id"],
        },
    },
]


def main() -> None:
    import json
    import sys

    print(json.dumps({"server": "stripe", "tools": [t["name"] for t in TOOLS]}), file=sys.stderr)
