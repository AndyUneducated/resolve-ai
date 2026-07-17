"""MCP server 注册中心 — 配置驱动的可插拔接入。

新增 SaaS = 加一个 MCP server entry，agent 不用动代码。
"""

from __future__ import annotations

from dataclasses import dataclass

from resolveai_api.config import get_settings


@dataclass
class McpServerSpec:
    name: str  # eg. "stripe"
    cmd: str  # stdio 启动命令
    transport: str = "stdio"  # 也支持 "http" 用于生产


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
