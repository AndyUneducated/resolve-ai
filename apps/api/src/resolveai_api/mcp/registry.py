"""MCP server registry for configuration-driven, pluggable integrations.

Add a SaaS integration by adding an MCP server entry without changing agent code.
"""

from __future__ import annotations

from dataclasses import dataclass

from resolveai_api.config import get_settings


@dataclass
class McpServerSpec:
    name: str  # eg. "stripe"
    cmd: str  # stdio startup command
    transport: str = "stdio"  # "http" is also supported in production


def default_servers() -> list[McpServerSpec]:
    """Return only servers with a non-empty cmd (unset servers are skipped)."""
    s = get_settings()
    transport = s.mcp_transport
    candidates = [
        ("zendesk", s.mcp_zendesk_cmd),
        ("stripe", s.mcp_stripe_cmd),
        ("slack", s.mcp_slack_cmd),
        ("salesforce", s.mcp_salesforce_cmd),
        ("intercom", s.mcp_intercom_cmd),
    ]
    return [McpServerSpec(name, cmd, transport) for name, cmd in candidates if cmd]
