"""Executor — invoke LangChain tools (MCP-backed) with capability enforcement.

每次工具调用都过：
  1. capability whitelist 检查（决策 4 · Layer 2，three tiers — M3 收紧）
     - read 工具默认放行（即使不在白名单）— 业务 Agent 通常需要读
     - write 工具必须显式 grant（agent 的 TOOL_WHITELIST）
     - destructive 工具必须显式 grant，并标 `audit=True` 供 Layer 3 cross-check
  2. sandbox scope metadata（决策 4 · Layer 2）
  3. 调用 LangChain BaseTool（由 mcp/loader.py 适配出来）

行业对齐：capability-based access control 与 OpenAI tool calling / Anthropic
computer-use / Bedrock Agents 的「最小权限 + 显式 grant」一致。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool

from resolveai_api.guardrails.exec_sandbox import ExecutionSandbox

logger = logging.getLogger(__name__)

_GATED_CAPABILITIES = ("write", "destructive")


@dataclass
class ExecutionResult:
    tool: str
    args: dict[str, Any]
    output: object
    duration_ms: float
    sandbox_violations: list[str] = field(default_factory=list)
    capability: str = "read"
    audit: bool = False


class Executor:
    """Wraps every tool call with capability + sandbox checks.

    `tools` is the per-Agent whitelist as `list[BaseTool]` (already filtered by
    [`ToolBelt.for_agent()`](../mcp/toolbelt.py) before being passed in).
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
        if capability in _GATED_CAPABILITIES and full not in whitelist:
            raise PermissionError(
                f"{capability} tool {full!r} not granted (whitelist={whitelist!r})"
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
        capability = self._capability(tool)
        audit = capability == "destructive"

        async with self.sandbox.scope(tool=full) as scope:
            output = await tool.ainvoke(args)

        if audit:
            # Layer 3 (M4) will cross-check these against output guardrails.
            logger.info(
                "destructive_tool_invoked tool=%s args_keys=%s",
                full,
                sorted(args.keys()),
            )

        return ExecutionResult(
            tool=full,
            args=args,
            output=output,
            duration_ms=scope.duration_ms,
            sandbox_violations=scope.violations,
            capability=capability,
            audit=audit,
        )
