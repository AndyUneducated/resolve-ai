"""MCP client — 统一调用所有 MCP server 的门面。

实际跑：
- dev：stdio 子进程
- prod：HTTP/SSE 连接到独立部署的 MCP server pod（可走 gVisor）
"""

from __future__ import annotations


class MCPClient:
    """TODO: 用 `mcp` SDK 实现 stdio / HTTP transport。"""

    def __init__(self) -> None:
        self._connected: dict[str, object] = {}

    async def call(self, *, full_name: str, args: dict[str, object]) -> object:
        """`full_name` 形如 "stripe.refund" — 拆出 server + tool。"""
        if "." not in full_name:
            raise ValueError(f"Tool name must be 'server.tool', got: {full_name}")
        server, tool = full_name.split(".", 1)
        # TODO: 真正连接 server，调 tool
        return {"_stub": True, "server": server, "tool": tool, "args": args}

    async def list_tools(self, server: str) -> list[dict[str, object]]:
        """MCP discovery — 标准 schema 列出 server 能力。"""
        return []
