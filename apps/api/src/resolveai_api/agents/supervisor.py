"""Supervisor — LangGraph 编排，跑 Triage → 业务 Agent → (可选) Escalation。

State checkpointing key = (tenant_id, customer_id, thread_id)，
对应决策 4 · Layer 4 记忆侧隔离。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Literal

from langgraph.graph import END, START, StateGraph

from resolveai_api.agents.billing import BillingAgent
from resolveai_api.agents.escalation import EscalationAgent
from resolveai_api.agents.state import GraphState
from resolveai_api.agents.technical import TechnicalAgent
from resolveai_api.agents.triage import TriageAgent
from resolveai_api.config import get_settings
from resolveai_api.core.executor import Executor
from resolveai_api.core.memory import Memory
from resolveai_api.core.planner import Planner
from resolveai_api.core.tool import ToolBelt
from resolveai_api.guardrails.input_filter import InputGuardrail
from resolveai_api.guardrails.output_filter import OutputGuardrail


def _build_agents() -> dict[str, object]:
    """工厂 — 用同一套四件套实现，注入到每个 Agent。"""
    planner = Planner()
    memory = Memory()
    toolbelt = ToolBelt()
    executor = Executor()
    common = {
        "planner": planner,
        "memory": memory,
        "toolbelt": toolbelt,
        "executor": executor,
    }
    return {
        "triage": TriageAgent.default(**common),
        "billing": BillingAgent.default(**common),
        "technical": TechnicalAgent.default(**common),
        "escalation": EscalationAgent.default(**common),
    }


def _route_after_triage(state: GraphState) -> Literal["billing", "technical", "escalation", END]:
    """Conditional edge — 按 Triage 输出的 intent 走分支。"""
    summary = state.get("ticket_summary", {})
    intent = summary.get("intent", "other")
    if intent in ("billing", "technical", "escalation"):
        return intent  # type: ignore[return-value]
    return END


class SupervisorGraph:
    """对外暴露的 stream() — 把 LangGraph 事件转换成 SSE event。"""

    def __init__(self) -> None:
        self.agents = _build_agents()
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

        # TODO: 接 PostgresSaver 做 checkpointing（决策 4 · Layer 4）
        return builder.compile()

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

        # ---- 决策 4 · Layer 1 输入 guardrails ----
        scrubbed, flags = await self.input_guard.scan_and_redact(message)
        if "blocked" in flags:
            yield {"type": "blocked", "data": json.dumps({"reason": flags})}
            return

        initial: GraphState = {
            "messages": [{"role": "user", "content": scrubbed}],
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "thread_id": thread_id or "",
            "tool_calls": [],
            "guardrail_flags": flags,
        }

        async for event in self.graph.astream(initial):
            for node_name, node_state in event.items():
                # ---- 决策 4 · Layer 3 输出 guardrails ----
                msgs = node_state.get("messages", []) if isinstance(node_state, dict) else []
                if msgs:
                    last = msgs[-1]
                    content = last.get("content", "") if isinstance(last, dict) else ""
                    safe, out_flags = await self.output_guard.scan(content)
                    yield {
                        "type": "agent_step",
                        "data": json.dumps(
                            {"agent": node_name, "content": safe, "flags": out_flags}
                        ),
                    }
        yield {"type": "done", "data": "{}"}


@lru_cache
def get_supervisor() -> SupervisorGraph:
    return SupervisorGraph()
