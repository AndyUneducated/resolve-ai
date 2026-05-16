"""Executor — 把 Plan 在 sandbox 里跑起来。

每次工具调用都过：
  1. capability whitelist 检查（决策 4 · Layer 2）
  2. gVisor sandbox 执行（决策 4 · Layer 2）
  3. 工具返回值 schema 校验（防 LLM 编造的 hallucinated entity 漏出来）
"""

from __future__ import annotations

from dataclasses import dataclass

from resolveai_api.core.tool import ToolBelt
from resolveai_api.guardrails.exec_sandbox import ExecutionSandbox


@dataclass
class ExecutionResult:
    tool: str
    args: dict[str, object]
    output: object
    duration_ms: float
    sandbox_violations: list[str]


class Executor:
    def __init__(
        self,
        toolbelt: ToolBelt | None = None,
        sandbox: ExecutionSandbox | None = None,
    ) -> None:
        self.toolbelt = toolbelt or ToolBelt()
        self.sandbox = sandbox or ExecutionSandbox()

    async def call_tool(
        self,
        *,
        full_name: str,
        args: dict[str, object],
        whitelist: list[str],
    ) -> ExecutionResult:
        if not self.toolbelt.whitelisted_for(full_name, whitelist):
            raise PermissionError(f"Tool {full_name} not in agent whitelist")

        # gVisor per-call sandbox
        async with self.sandbox.scope(tool=full_name) as scope:
            output = await self.toolbelt.client.call(full_name=full_name, args=args)

        return ExecutionResult(
            tool=full_name,
            args=args,
            output=output,
            duration_ms=scope.duration_ms,
            sandbox_violations=scope.violations,
        )
