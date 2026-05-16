"""Supervisor — LangGraph orchestration: Triage → vertical agent → END.

Compile-time wiring:
- `checkpointer` (AsyncPostgresSaver | MemorySaver) for stateful handoff & resume.
- `toolbelt` (M3) or legacy `mcp_tools` list: discovered MCP tools, sliced per-agent.

Per-request:
- `thread_id = "{tenant}::{customer}::{thread}"` is namespaced for cross-tenant
  isolation (decision 4 · Layer 4).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from resolveai_api.agents.billing import TOOL_WHITELIST as BILLING_WHITELIST
from resolveai_api.agents.billing import BillingAgent
from resolveai_api.agents.escalation import TOOL_WHITELIST as ESCALATION_WHITELIST
from resolveai_api.agents.escalation import EscalationAgent
from resolveai_api.agents.state import GraphState
from resolveai_api.agents.technical import TOOL_WHITELIST as TECHNICAL_WHITELIST
from resolveai_api.agents.technical import TechnicalAgent
from resolveai_api.agents.triage import TriageAgent
from resolveai_api.config import get_settings
from resolveai_api.core.executor import Executor
from resolveai_api.guardrails.input_filter import InputGuardrail
from resolveai_api.guardrails.memory_isolator import MemoryIsolator
from resolveai_api.guardrails.output_filter import OutputGuardrail
from resolveai_api.mcp.toolbelt import ToolBelt


def _build_agents(toolbelt: ToolBelt) -> dict[str, object]:
    executor = Executor()
    return {
        "triage": TriageAgent.default(tools=[], executor=executor),
        "billing": BillingAgent.default(
            tools=toolbelt.for_agent(BILLING_WHITELIST),
            executor=executor,
        ),
        "technical": TechnicalAgent.default(
            tools=toolbelt.for_agent(TECHNICAL_WHITELIST),
            executor=executor,
        ),
        "escalation": EscalationAgent.default(
            tools=toolbelt.for_agent(ESCALATION_WHITELIST),
            executor=executor,
        ),
    }


def _route_after_triage(state: GraphState) -> Literal["billing", "technical", "escalation", END]:
    summary = state.get("ticket_summary", {})
    intent = summary.get("intent", "other")
    if intent in ("billing", "technical", "escalation"):
        return intent  # type: ignore[return-value]
    return END


def _extract_text(msg: BaseMessage | dict | object) -> str:
    if isinstance(msg, BaseMessage):
        content = msg.content
    elif isinstance(msg, dict):
        content = msg.get("content", "")
    else:
        content = ""
    if isinstance(content, str):
        return content
    return str(content)


class SupervisorGraph:
    """LangGraph supervisor wired with checkpointer + a ToolBelt.

    `toolbelt` is the M3 surface; `mcp_tools` is kept for backwards compatibility
    so existing tests (and any caller that already filtered tools) keep working.
    """

    def __init__(
        self,
        *,
        checkpointer: BaseCheckpointSaver,
        toolbelt: ToolBelt | None = None,
        mcp_tools: list[BaseTool] | None = None,
    ) -> None:
        if toolbelt is None:
            toolbelt = ToolBelt(mcp_tools or [])
        self.checkpointer = checkpointer
        self.toolbelt = toolbelt
        self.agents = _build_agents(toolbelt)
        self.input_guard = InputGuardrail()
        self.output_guard = OutputGuardrail()
        self.graph = self._build_graph()

    def _build_graph(self):
        builder: StateGraph = StateGraph(GraphState)

        builder.add_node("triage", self.agents["triage"].run)  # type: ignore[attr-defined]
        builder.add_node("billing", self.agents["billing"].run)  # type: ignore[attr-defined]
        builder.add_node("technical", self.agents["technical"].run)  # type: ignore[attr-defined]
        builder.add_node("escalation", self.agents["escalation"].run)  # type: ignore[attr-defined]

        builder.add_edge(START, "triage")
        builder.add_conditional_edges(
            "triage",
            _route_after_triage,
            {
                "billing": "billing",
                "technical": "technical",
                "escalation": "escalation",
                END: END,
            },
        )
        builder.add_edge("billing", END)
        builder.add_edge("technical", END)
        builder.add_edge("escalation", END)

        return builder.compile(checkpointer=self.checkpointer)

    async def stream(
        self,
        *,
        message: str,
        customer_id: str,
        tenant_id: str | None,
        thread_id: str | None,
    ) -> AsyncIterator[dict[str, str]]:
        settings = get_settings()
        tenant_id = tenant_id or settings.default_tenant_id
        thread_id = thread_id or "default"
        namespace = MemoryIsolator.namespace(tenant_id, customer_id, thread_id)

        # Layer 1 input guardrails
        scrubbed, flags = await self.input_guard.scan_and_redact(message)
        if "blocked" in flags:
            yield {"type": "blocked", "data": json.dumps({"reason": flags})}
            return

        initial: GraphState = {
            "messages": [HumanMessage(content=scrubbed)],
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "thread_id": thread_id,
            "tool_calls": [],
            "guardrail_flags": flags,
        }
        config = {"configurable": {"thread_id": namespace}}

        async for event in self.graph.astream(initial, config=config):
            for node_name, node_state in event.items():
                msgs = (
                    node_state.get("messages", []) if isinstance(node_state, dict) else []
                )
                if not msgs:
                    continue
                content = _extract_text(msgs[-1])
                # Layer 3 output guardrails
                safe, out_flags = await self.output_guard.scan(content)
                yield {
                    "type": "agent_step",
                    "data": json.dumps(
                        {"agent": node_name, "content": safe, "flags": out_flags}
                    ),
                }
        yield {"type": "done", "data": "{}"}
