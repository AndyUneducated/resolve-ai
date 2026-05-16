"""Technical Agent — pulls support context via MCP tools (M3).

M3 范围：还没接 Hybrid Retrieval（M6 的活），但已经能用 MCP 工具拉真实/mock
context：Zendesk ticket 历史 + Intercom conversation。

Plan-and-Execute LLM 编排留给 M6（接 KB hybrid retrieval 之后）。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from resolveai_api.agents.base import AgentConfig, BaseAgent
from resolveai_api.agents.state import GraphState

SYSTEM_PROMPT = """\
You are the Technical Agent. You handle bug reports, feature questions, and configuration help.

Process (Plan-and-Execute):
1. Search internal KB / FAQ via hybrid retrieval first.
2. If reproducible from logs / past tickets, propose a fix.
3. If not reproducible OR SLA-impacting → handoff to escalation.

Hard rules:
- NEVER promise features that are not in the product changelog.
- ALWAYS cite KB doc IDs when giving instructions.
"""

TOOL_WHITELIST = [
    "zendesk.get_ticket_history",
    "zendesk.update_ticket",
    "intercom.get_conversation",
    "kb.search",  # placeholder; lands in M6
]


def _find_tool(tools: list[BaseTool], full_name: str) -> BaseTool | None:
    for tool in tools:
        if (tool.metadata or {}).get("full_name") == full_name:
            return tool
    return None


class TechnicalAgent(BaseAgent):
    @classmethod
    def default(cls, **kwargs: Any) -> TechnicalAgent:
        from resolveai_api.config import get_settings

        settings = get_settings()
        config = AgentConfig(
            name="technical",
            model=settings.vertical_model,
            system_prompt=SYSTEM_PROMPT,
            tool_whitelist=list(TOOL_WHITELIST),
        )
        return cls(config=config, **kwargs)

    async def run(self, state: GraphState) -> GraphState:
        customer_id = state.get("customer_id") or ""
        tool_calls = list(state.get("tool_calls") or [])
        context_lines: list[str] = []

        history_tool = _find_tool(self.tools, "zendesk.get_ticket_history")
        if history_tool is not None and customer_id:
            try:
                result = await self.executor.call_tool(
                    tool=history_tool,
                    args={"customer_id": customer_id},
                    whitelist=self.config.tool_whitelist,
                )
                tickets = result.output if isinstance(result.output, list) else []
                tool_calls.append(
                    {
                        "step": "zendesk.get_ticket_history",
                        "observation": json.dumps(tickets, default=str)[:1000],
                    }
                )
                if tickets:
                    titles = ", ".join(str(t.get("subject", "")) for t in tickets[:3])
                    context_lines.append(
                        f"Found {len(tickets)} prior tickets for {customer_id}: {titles}"
                    )
                else:
                    context_lines.append(f"No prior tickets found for {customer_id}.")
            except Exception as exc:  # best effort context fetch
                tool_calls.append({"step": "zendesk.get_ticket_history", "error": str(exc)})

        if not context_lines:
            context_lines.append(
                "Hybrid KB retrieval lands in M6; no historical context available yet."
            )

        message = (
            "Technical Agent (M3 preview — full plan-and-execute reasoning lands in M6).\n"
            + "\n".join(f"- {line}" for line in context_lines)
        )
        return {
            **state,
            "messages": [AIMessage(content=message)],
            "tool_calls": tool_calls,
        }
