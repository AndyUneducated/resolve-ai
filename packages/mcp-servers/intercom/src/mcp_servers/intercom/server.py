"""Intercom Mock MCP Server."""

from __future__ import annotations

from typing import Any

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
        "description": "Apply a tag to an Intercom user.",
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


def main() -> None:
    import json
    import sys

    print(
        json.dumps({"server": "intercom", "tools": [t["name"] for t in TOOLS]}),
        file=sys.stderr,
    )
