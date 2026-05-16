"""Executor — invoke LangChain tools (MCP-backed) with capability enforcement.

每次工具调用都过：
  1. capability whitelist 检查（决策 4 · Layer 2）
     - destructive 工具必须在 agent 的 whitelist 中
     - read 工具默认放行（即使不在白名单）— 业务 Agent 通常需要读
  2. gVisor sandbox scope（决策 4 · Layer 2 · placeholder until M4）
  3. 调用 LangChain BaseTool（由 mcp/loader.py 适配出来）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool

from resolveai_api.guardrails.exec_sandbox import ExecutionSandbox


@dataclass
class ExecutionResult:
    tool: str
    args: dict[str, Any]
    output: object
    duration_ms: float
    sandbox_violations: list[str] = field(default_factory=list)


class Executor:
    """Wraps every tool call with capability + sandbox checks.

    `tools` is the per-Agent whitelist as `list[BaseTool]` (already filtered by
    [`mcp/loader.py`](../mcp/loader.py).filter_by_whitelist before being passed in).
    """

    def __init__(self, sandbox: ExecutionSandbox | None = None) -> None:
        self.sandbox = sandbox or ExecutionSandbox()

    @staticmethod
    def _full_name(tool: BaseTool) -> str:
        meta = tool.metadata or {}
        return str(meta.get("full_name") or tool.name)

    @staticmethod
    def _capability(tool: BaseTool) -> str:
        return str((tool.metadata or {}).get("capability", "read"))

    def _check_capability(self, tool: BaseTool, whitelist: list[str]) -> None:
        capability = self._capability(tool)
        full = self._full_name(tool)
        if capability == "destructive" and full not in whitelist:
            raise PermissionError(
                f"Destructive tool {full!r} not granted (whitelist={whitelist!r})"
            )

    async def call_tool(
        self,
        *,
        tool: BaseTool,
        args: dict[str, Any],
        whitelist: list[str],
    ) -> ExecutionResult:
        self._check_capability(tool, whitelist)

        full = self._full_name(tool)
        async with self.sandbox.scope(tool=full) as scope:
            output = await tool.ainvoke(args)

        return ExecutionResult(
            tool=full,
            args=args,
            output=output,
            duration_ms=scope.duration_ms,
            sandbox_violations=scope.violations,
        )
