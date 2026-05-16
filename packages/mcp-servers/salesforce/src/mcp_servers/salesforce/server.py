"""Salesforce Mock MCP Server."""

from __future__ import annotations

from typing import Any

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_account",
        "description": "Get Salesforce account by customer_id.",
        "capability": "read",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "update_opportunity",
        "description": "Update an opportunity stage / amount.",
        "capability": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "opportunity_id": {"type": "string"},
                "stage": {"type": "string"},
                "amount": {"type": "number"},
            },
            "required": ["opportunity_id"],
        },
    },
]


def main() -> None:
    import json
    import sys

    print(
        json.dumps({"server": "salesforce", "tools": [t["name"] for t in TOOLS]}),
        file=sys.stderr,
    )
