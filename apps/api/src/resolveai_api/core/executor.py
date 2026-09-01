"""Executor — invoke LangChain tools (MCP-backed) with capability enforcement.

Every tool call passes through:
  1. A capability-whitelist check (Decision 4 · Layer 2, tightened to three
     tiers in M3)
     - read tools are allowed by default, even when absent from the whitelist
     - write tools require an explicit grant in the agent's TOOL_WHITELIST
     - destructive tools require an explicit grant and set `audit=True` for
       Layer 3 cross-checking
  2. Sandbox scope metadata (Decision 4 · Layer 2)
  3. Invocation of the LangChain BaseTool adapted by mcp/loader.py

This capability-based access control follows the least-privilege and explicit-
grant model used by OpenAI tool calling, Anthropic computer use, and Bedrock Agents.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool

from resolveai_api.guardrails.exec_sandbox import ExecutionSandbox
from resolveai_api.observability.tracing import get_tracer, span

logger = logging.getLogger(__name__)

_TRACER = get_tracer("resolveai.executor")

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
    # HITL gate (M12): "none" | "pending" (parked, not executed) | "denied" (blocked)
    approval: str = "none"


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

    def _approval_gate(self, full: str, args: dict[str, Any]) -> ExecutionResult | None:
        """Return a park/deny `ExecutionResult` (tool NOT run), or None to proceed.

        No-op unless an enabled `ApprovalContext` is active (`APPROVAL_MODE`), so
        the default path is byte-identical to pre-M12.
        """
        from resolveai_api.core.approvals import (
            ApprovalStatus,
            current_approval_context,
            get_approval_store,
        )

        ctx = current_approval_context()
        if ctx is None or not ctx.enabled:
            return None

        request = get_approval_store().require(
            thread_ref=ctx.thread_ref,
            tenant_id=ctx.tenant_id,
            tool=full,
            capability="destructive",
            args=args,
        )
        if request.status is ApprovalStatus.APPROVED:
            return None  # proceed; edited args applied by _approved_args
        if request.status is ApprovalStatus.DENIED:
            return ExecutionResult(
                tool=full,
                args=args,
                output=f"[blocked] human reviewer denied {full} (approval {request.id})",
                duration_ms=0.0,
                capability="destructive",
                audit=True,
                approval="denied",
            )
        # PENDING — park it and surface to the Supervisor via the request context.
        if request not in ctx.pending:
            ctx.pending.append(request)
        return ExecutionResult(
            tool=full,
            args=args,
            output=f"[awaiting human approval] {full} parked as approval {request.id}",
            duration_ms=0.0,
            capability="destructive",
            audit=True,
            approval="pending",
        )

    def _approved_args(self, full: str, args: dict[str, Any]) -> dict[str, Any]:
        """When a human `edit`-approved the call, execute with the edited args."""
        from resolveai_api.core.approvals import current_approval_context, get_approval_store

        ctx = current_approval_context()
        if ctx is None or not ctx.enabled:
            return args
        request = get_approval_store().require(
            thread_ref=ctx.thread_ref,
            tenant_id=ctx.tenant_id,
            tool=full,
            capability="destructive",
            args=args,
        )
        return request.effective_args()

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

        from resolveai_api.core.usage import looks_like_error, record_tool_call

        # ---- HITL approval gate (M12) — destructive tools only, opt-in ----
        if capability == "destructive":
            gate = self._approval_gate(full, args)
            if gate is not None:
                record_tool_call(
                    tool=full,
                    args=args,
                    output=gate.output,
                    is_error=False,
                    duration_ms=0.0,
                )
                return gate
            args = self._approved_args(full, args)

        with span(
            _TRACER,
            "tool.call",
            attributes={"tool": full, "capability": capability, "audit": audit},
        ) as tool_span:
            try:
                async with self.sandbox.scope(tool=full, capability=capability) as scope:
                    output = await tool.ainvoke(args)
            except Exception as exc:
                # Record the failed call for ablation tool-error accounting, then
                # re-raise so callers keep their existing error handling.
                record_tool_call(
                    tool=full,
                    args=args,
                    output=f"{type(exc).__name__}: {exc}",
                    is_error=True,
                    duration_ms=0.0,
                )
                if tool_span is not None:
                    try:
                        tool_span.set_attribute("error", True)
                        tool_span.set_attribute("error_type", type(exc).__name__)
                    except Exception:  # pragma: no cover - defensive
                        pass
                raise

            is_error = looks_like_error(output)
            record_tool_call(
                tool=full,
                args=args,
                output=output,
                is_error=is_error,
                duration_ms=scope.duration_ms,
            )
            if tool_span is not None:
                try:
                    tool_span.set_attribute("error", is_error)
                    tool_span.set_attribute("duration_ms", scope.duration_ms)
                except Exception:  # pragma: no cover - defensive
                    pass

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
