"""Slack Mock MCP Server.

工具：
- notify_team(channel, message, mention?) — Escalation 用
- post_message(channel, message)
"""

from __future__ import annotations

from typing import Any

TOOLS: list[dict[str, Any]] = [
    {
        "name": "notify_team",
        "description": "Notify on-call team channel with an optional @-mention.",
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
        "description": "Post a generic message to a channel.",
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


def main() -> None:
    import json
    import sys

    print(json.dumps({"server": "slack", "tools": [t["name"] for t in TOOLS]}), file=sys.stderr)
