"""ToolBelt — 包装 MCP client，带 capability whitelist enforcement。

决策 4 · Layer 2 — 工具能力白名单：
- read vs write 分级（如 Stripe.list_charges 默认允许，Stripe.refund 需 explicit grant）
- 网络出口白名单 + 文件系统隔离（在 Executor 里实现）
"""

from __future__ import annotations

from dataclasses import dataclass

from resolveai_api.mcp.client import MCPClient


@dataclass
class ToolSpec:
    """单个 MCP tool 的元数据 + 安全标签。"""

    server: str  # eg. "stripe"
    name: str  # eg. "refund"
    capability: str  # "read" | "write" | "destructive"
    schema: dict[str, object]


class ToolBelt:
    """所有 MCP tool 的注册中心 + 调用门面。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._client: MCPClient | None = None

    @property
    def client(self) -> MCPClient:
        if self._client is None:
            self._client = MCPClient()
        return self._client

    def register(self, spec: ToolSpec) -> None:
        full = f"{spec.server}.{spec.name}"
        self._tools[full] = spec

    def lookup(self, full_name: str) -> ToolSpec | None:
        return self._tools.get(full_name)

    def whitelisted_for(self, full_name: str, whitelist: list[str]) -> bool:
        """决策 4 · Layer 2 — 当前 Agent 是否被允许调这个工具。"""
        return full_name in whitelist
